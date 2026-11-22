import { useCallback, useEffect, useState } from "react";
import {
  createAllocations,
  fetchPoSchedule,
  fetchSchedulingOperators,
  reviseAllocationQty,
  SchedulingApiError,
  type Allocation,
  type LineSchedule,
  type ScreenKind,
} from "../api/scheduling";
import {
  designEvidenceContentUrl,
  fetchDesignState,
  type DesignStateResponse,
} from "../api/workflow";
import { newIdempotencyKey, type OperationalTask } from "../api/operational";
import { formatDateDisplay } from "../lib/formatDate";
import { useMobileOpsActive } from "../mobile/MobileOpsContext";
import { WorkSheetCard, WorkSheetStackRow } from "./compactTaskSheet";
import {
  operatorCandidateLabel,
  operatorsBlockerMessage,
  operatorsLoadFromError,
  operatorsLoadFromSuccess,
  type OperatorsLoadState,
} from "./schedulingOperators";
import {
  allocationCanReviseQty,
  allocationReviseBounds,
} from "./teamTaskUi";

type Props = {
  task: OperationalTask;
  /** false = penjadwalan ulang / hanya revisi + lihat. */
  canAllocate: boolean;
  canRevise: boolean;
  busy: boolean;
  setBusy: (v: boolean) => void;
  onError: (msg: string | null) => void;
  onNotice: (msg: string | null) => void;
  onSuccess: (message: string) => void;
  onChanged: () => Promise<void>;
  onRegisterDesignOpen?: (fn: (() => void) | null) => void;
};

function qty(n: number): string {
  return n.toLocaleString("id-ID");
}

function allocStatusLabel(status: string): string {
  if (status === "active") return "Aktif";
  if (status === "released") return "Dilepas";
  return status || "—";
}

function allocStatusClass(status: string): string {
  if (status === "active") return "ep-task-status is-sebagian";
  if (status === "released") return "ep-task-status is-belum";
  return "ep-task-status is-wait";
}

function operatorLabel(a: {
  operator_email: string;
  operator_display_name?: string | null;
}): string {
  return operatorCandidateLabel({
    email: a.operator_email,
    display_name: a.operator_display_name,
  });
}

/**
 * Form alokasi Kepala Produksi — semantik sama ScheduleApiPanel
 * (createAllocations / reviseAllocationQty). Bukan confirm Qty generik.
 */
export function TeamTaskSchedulingForm({
  task,
  canAllocate,
  canRevise,
  busy,
  setBusy,
  onError,
  onNotice,
  onSuccess,
  onChanged,
  onRegisterDesignOpen,
}: Props) {
  const mobileSheet = useMobileOpsActive();
  const [lines, setLines] = useState<LineSchedule[]>([]);
  const [lineId, setLineId] = useState(task.ref.accurate_line_id || "");
  const [line, setLine] = useState<LineSchedule | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [operatorsState, setOperatorsState] = useState<OperatorsLoadState>({
    status: "idle",
  });
  const [operatorEmail, setOperatorEmail] = useState("");
  const [allocQty, setAllocQty] = useState("");
  const [scheduleDate, setScheduleDate] = useState("");
  const [printColor, setPrintColor] = useState("");
  const [screenKind, setScreenKind] = useState<ScreenKind | "">("");
  const [designOpen, setDesignOpen] = useState(false);
  const [designLoading, setDesignLoading] = useState(false);
  const [designError, setDesignError] = useState<string | null>(null);
  const [designState, setDesignState] = useState<DesignStateResponse | null>(
    null,
  );
  const [designContentOk, setDesignContentOk] = useState(false);
  const [reviseTarget, setReviseTarget] = useState<{
    alloc: Allocation;
    line: LineSchedule;
  } | null>(null);
  const [reviseQty, setReviseQty] = useState("");
  const [reviseReason, setReviseReason] = useState("");
  const [reviseError, setReviseError] = useState<string | null>(null);

  const poId = task.ref.accurate_po_id;

  const load = useCallback(
    async (preferLineId?: string) => {
      if (!poId) {
        setLines([]);
        setLine(null);
        setLoadError("Referensi PO penjadwalan tidak lengkap.");
        setLoading(false);
        return;
      }
      setLoading(true);
      setLoadError(null);
      try {
        const res = await fetchPoSchedule(poId);
        setLines(res.lines);
        const want =
          preferLineId ||
          task.ref.accurate_line_id ||
          "";
        const preferred =
          res.lines.find((l) => l.accurate_line_id === want) ||
          res.lines[0] ||
          null;
        if (preferred) {
          setLineId(preferred.accurate_line_id);
          setLine(preferred);
        } else {
          setLine(null);
          setLoadError("Baris jadwal Cetak tidak ditemukan untuk PO ini.");
        }
      } catch (err) {
        setLines([]);
        setLine(null);
        setLoadError(
          err instanceof Error ? err.message : "Gagal memuat jadwal alokasi.",
        );
      } finally {
        setLoading(false);
      }
    },
    [poId, task.ref.accurate_line_id],
  );

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const found = lines.find((l) => l.accurate_line_id === lineId) || null;
    if (found) setLine(found);
  }, [lines, lineId]);

  useEffect(() => {
    if (!canAllocate && !canRevise) {
      setOperatorsState({ status: "idle" });
      return;
    }
    let cancelled = false;
    setOperatorsState({ status: "loading" });
    void fetchSchedulingOperators()
      .then((res) => {
        if (cancelled) return;
        setOperatorsState(operatorsLoadFromSuccess(res.items));
      })
      .catch((err) => {
        if (cancelled) return;
        setOperatorsState(operatorsLoadFromError(err));
      });
    return () => {
      cancelled = true;
    };
  }, [canAllocate, canRevise]);

  async function onAllocate() {
    if (!line || !poId) return;
    const selectedLineId = line.accurate_line_id;
    const value = Number(allocQty);
    const color = printColor.trim();
    if (!selectedLineId) {
      onError("Item line wajib dipilih.");
      return;
    }
    if (!operatorEmail.trim()) {
      onError("Operator wajib dipilih dari Master Pengguna.");
      return;
    }
    const op =
      operatorsState.status === "ok"
        ? operatorsState.items.find((o) => o.email === operatorEmail)
        : undefined;
    if (!op || !(op.email || "").trim()) {
      onError(
        "Operator tidak memiliki identifier sah di Master Pengguna.",
      );
      return;
    }
    if (!Number.isFinite(value) || value <= 0) {
      onError("Qty alokasi wajib angka > 0.");
      return;
    }
    if (value > line.qty_unallocated) {
      onError(
        `Qty alokasi melebihi sisa belum terjadwal (${qty(line.qty_unallocated)}).`,
      );
      return;
    }
    if (!scheduleDate.trim()) {
      onError("Tanggal pengerjaan wajib diisi.");
      return;
    }
    if (!color) {
      onError("Warna cetak wajib diisi.");
      return;
    }
    if (screenKind !== "Besar" && screenKind !== "Kecil") {
      onError("Jenis screen wajib Besar atau Kecil.");
      return;
    }
    setBusy(true);
    onError(null);
    onNotice(null);
    try {
      const res = await createAllocations(
        poId,
        selectedLineId,
        [
          {
            operator_email: op.email,
            qty: value,
            print_color: color,
            screen_kind: screenKind,
          },
        ],
        newIdempotencyKey(`alloc:${selectedLineId}`),
        scheduleDate.trim(),
      );
      const msg = res.duplicate
        ? "Perintah sama sudah tercatat — jadwal tidak digandakan."
        : `Alokasi ${res.allocations[0]?.allocation_id || ""} tersimpan.`;
      onNotice(msg);
      onSuccess(msg);
      setOperatorEmail("");
      setAllocQty("");
      setPrintColor("");
      setScreenKind("");
      await load(selectedLineId);
      await onChanged();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Alokasi gagal.");
    } finally {
      setBusy(false);
    }
  }

  function openRevise(alloc: Allocation, parent: LineSchedule) {
    setReviseTarget({ alloc, line: parent });
    setReviseQty(String(alloc.qty));
    setReviseReason("");
    setReviseError(null);
    onError(null);
  }

  function closeRevise() {
    if (busy) return;
    setReviseTarget(null);
    setReviseQty("");
    setReviseReason("");
    setReviseError(null);
  }

  async function submitRevise() {
    if (!reviseTarget) return;
    const { alloc, line: parent } = reviseTarget;
    const bounds = allocationReviseBounds({
      qty: alloc.qty,
      qty_printed: alloc.qty_printed,
      qty_revise_min: alloc.qty_revise_min,
      qty_revise_max: alloc.qty_revise_max,
      lineQtyTarget: parent.qty_target,
      otherActiveQty: Math.max(
        parent.qty_allocated - (alloc.status === "active" ? alloc.qty : 0),
        0,
      ),
    });
    const value = Number(reviseQty);
    if (!Number.isFinite(value) || value < 0 || value !== Math.trunc(value)) {
      setReviseError("Qty baru harus bilangan bulat nonnegatif.");
      return;
    }
    if (value < bounds.min) {
      setReviseError(
        `Qty baru tidak boleh lebih kecil dari minimum revisi (${qty(bounds.min)}).`,
      );
      return;
    }
    if (value > bounds.max) {
      setReviseError(
        `Qty baru tidak boleh melebihi maksimum revisi (${qty(bounds.max)}).`,
      );
      return;
    }
    if (!reviseReason.trim()) {
      setReviseError("Alasan revisi wajib diisi.");
      return;
    }
    setBusy(true);
    setReviseError(null);
    onError(null);
    onNotice(null);
    try {
      await reviseAllocationQty(alloc.allocation_id, value, reviseReason.trim());
      const msg = `Alokasi ${alloc.allocation_id} direvisi menjadi ${qty(value)}.`;
      setReviseTarget(null);
      setReviseQty("");
      setReviseReason("");
      onNotice(msg);
      onSuccess(msg);
      await load(lineId);
      await onChanged();
    } catch (err) {
      const msg =
        err instanceof SchedulingApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Revisi gagal.";
      setReviseError(msg);
      onError(msg);
    } finally {
      setBusy(false);
    }
  }

  const onOpenDesign = useCallback(() => {
    if (!poId) {
      onError("PO tidak tersedia untuk melihat desain.");
      return;
    }
    setDesignOpen(true);
    setDesignLoading(true);
    setDesignError(null);
    setDesignState(null);
    setDesignContentOk(false);
    void (async () => {
      try {
        const state = await fetchDesignState(poId);
        setDesignState(state);
        const eid = state.latest?.evidence_id;
        if (eid) {
          const probe = await fetch(designEvidenceContentUrl(poId, eid), {
            credentials: "include",
            method: "GET",
            headers: { Range: "bytes=0-0" },
          });
          setDesignContentOk(probe.ok || probe.status === 206);
        }
      } catch (err) {
        setDesignError(
          err instanceof Error ? err.message : "Gagal memuat status desain.",
        );
      } finally {
        setDesignLoading(false);
      }
    })();
  }, [poId, onError]);

  useEffect(() => {
    onRegisterDesignOpen?.(onOpenDesign);
    return () => onRegisterDesignOpen?.(null);
  }, [onRegisterDesignOpen, onOpenDesign]);

  const isReschedule = task.task_type === "penjadwalan-ulang";
  const usableOperators =
    operatorsState.status === "ok" ? operatorsState.items : [];
  const operatorsError = operatorsBlockerMessage(operatorsState);
  const operatorsLoading = operatorsState.status === "loading";
  const formBlocked =
    !canAllocate ||
    operatorsLoading ||
    Boolean(operatorsError) ||
    usableOperators.length === 0 ||
    !line?.scheduling_unlocked;

  function itemNameForLine(l: LineSchedule): string {
    return l.item_name || "Nama barang belum tersedia";
  }

  const latest = designState?.latest ?? null;
  const evidenceId = latest?.evidence_id || "";
  const contentUrl =
    poId && evidenceId ? designEvidenceContentUrl(poId, evidenceId) : "";
  const isPdf =
    (latest?.filename || "").toLowerCase().endsWith(".pdf") ||
    false;
  const reviseBounds = reviseTarget
    ? allocationReviseBounds({
        qty: reviseTarget.alloc.qty,
        qty_printed: reviseTarget.alloc.qty_printed,
        qty_revise_min: reviseTarget.alloc.qty_revise_min,
        qty_revise_max: reviseTarget.alloc.qty_revise_max,
        lineQtyTarget: reviseTarget.line.qty_target,
        otherActiveQty: Math.max(
          reviseTarget.line.qty_allocated -
            (reviseTarget.alloc.status === "active"
              ? reviseTarget.alloc.qty
              : 0),
          0,
        ),
      })
    : null;

  return (
    <div className="ep-task-schedule" data-testid="task-scheduling-form">
      {loading ? (
        <p className="ep-notice ep-notice-compact">Memuat data jadwal…</p>
      ) : null}
      {loadError ? (
        <p className="ep-error" role="alert">
          {loadError}
        </p>
      ) : null}
      {lines.length > 0 || line ? (
        <>
          {mobileSheet ? (
            <div className="ep-ws-sched-toolbar">
              <button
                type="button"
                className="ep-btn sm ep-btn-design"
                data-testid="sched-view-design-body"
                title="Lihat desain PO aktif"
                onClick={() => onOpenDesign()}
              >
                Lihat Design
              </button>
            </div>
          ) : null}

          <p className="ep-sectionlabel">Item Cetak</p>
          {mobileSheet ? (
            <div
              className="ep-ws-stack-list"
              data-testid="sched-items-cards"
            >
              {lines.map((l) => (
                <WorkSheetCard
                  key={l.accurate_line_id}
                  className={
                    l.accurate_line_id === lineId ? "is-active-line" : undefined
                  }
                  testId="sched-item-card"
                >
                  <button
                    type="button"
                    className="ep-ws-sched-item-pick"
                    onClick={() => setLineId(l.accurate_line_id)}
                  >
                    <span
                      className="ep-sched-item-name"
                      data-testid="sched-item-name"
                    >
                      {itemNameForLine(l)}
                    </span>
                    <span className="ep-sched-item-code">
                      {l.accurate_line_id}
                    </span>
                  </button>
                  <WorkSheetStackRow
                    label="Qty PO"
                    value={<span className="ep-num">{qty(l.qty_target)}</span>}
                  />
                  <WorkSheetStackRow
                    label="Qty Alokasi Aktif"
                    value={
                      <span className="ep-num">{qty(l.qty_allocated)}</span>
                    }
                  />
                  <WorkSheetStackRow
                    label="Qty Belum Dialokasikan"
                    value={
                      <span className="ep-num">{qty(l.qty_unallocated)}</span>
                    }
                    emphasize
                  />
                  <WorkSheetStackRow
                    label="Qty Cetak Terkonfirmasi"
                    value={
                      <span className="ep-num">{qty(l.qty_printed)}</span>
                    }
                  />
                </WorkSheetCard>
              ))}
            </div>
          ) : (
            <div
              className="ep-sched-items-scroll"
              data-testid="sched-items-scroll"
            >
              <table
                className="ep-table ep-table-compact ep-sched-items-table"
                data-testid="sched-items-table"
              >
                <colgroup>
                  <col className="ep-sched-col-name" />
                  <col className="ep-sched-col-qty" />
                  <col className="ep-sched-col-qty" />
                  <col className="ep-sched-col-qty" />
                  <col className="ep-sched-col-qty" />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col">Nama Barang</th>
                    <th scope="col">Qty PO</th>
                    <th scope="col">
                      Qty Alokasi
                      <br />
                      Aktif
                    </th>
                    <th scope="col">
                      Qty Belum
                      <br />
                      Dialokasikan
                    </th>
                    <th scope="col">
                      Qty Cetak
                      <br />
                      Terkonfirmasi
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {lines.map((l) => (
                    <tr
                      key={l.accurate_line_id}
                      className={
                        l.accurate_line_id === lineId
                          ? "is-active-line"
                          : undefined
                      }
                      onClick={() => setLineId(l.accurate_line_id)}
                    >
                      <td>
                        <span
                          className="ep-sched-item-name"
                          data-testid="sched-item-name"
                        >
                          {itemNameForLine(l)}
                        </span>
                        <span className="ep-sched-item-code">
                          {l.accurate_line_id}
                        </span>
                      </td>
                      <td className="ep-num">{qty(l.qty_target)}</td>
                      <td className="ep-num">{qty(l.qty_allocated)}</td>
                      <td className="ep-num">{qty(l.qty_unallocated)}</td>
                      <td className="ep-num">{qty(l.qty_printed)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <p className="ep-sectionlabel ep-sched-section-title">
            Daftar Alokasi
          </p>
          {lines.every((l) => l.allocations.length === 0) ? (
            <p className="ep-empty ep-empty-inline">Belum ada alokasi.</p>
          ) : mobileSheet ? (
            <div
              className="ep-ws-stack-list"
              data-testid="sched-alloc-cards"
            >
              {lines.flatMap((l) =>
                l.allocations.map((a) => (
                  <WorkSheetCard
                    key={a.allocation_id}
                    testId="sched-alloc-card"
                  >
                    <WorkSheetStackRow
                      label="Operator"
                      value={
                        <span
                          title={
                            (a.operator_display_name || "").trim() ||
                            "Nama belum tersedia"
                          }
                        >
                          {operatorLabel(a)}
                        </span>
                      }
                      emphasize
                    />
                    <WorkSheetStackRow
                      label="Item"
                      value={
                        <>
                          <span className="ep-sched-item-name">
                            {itemNameForLine(l)}
                          </span>
                          <span className="ep-sched-item-code">
                            {a.accurate_line_id}
                          </span>
                        </>
                      }
                    />
                    <WorkSheetStackRow
                      label="Qty Alokasi"
                      value={<span className="ep-num">{qty(a.qty)}</span>}
                      emphasize
                    />
                    <WorkSheetStackRow
                      label="Tanggal Pengerjaan"
                      value={formatDateDisplay(a.schedule_date) || "—"}
                    />
                    <WorkSheetStackRow
                      label="Warna Cetak"
                      value={a.print_color?.trim() || "—"}
                    />
                    <WorkSheetStackRow
                      label="Jenis Screen"
                      value={a.screen_kind?.trim() || "—"}
                    />
                    <WorkSheetStackRow
                      label="Qty Cetak"
                      value={
                        <span className="ep-num">{qty(a.qty_printed || 0)}</span>
                      }
                    />
                    <WorkSheetStackRow
                      label="Status"
                      value={
                        <span className={allocStatusClass(a.status)}>
                          {allocStatusLabel(a.status)}
                        </span>
                      }
                    />
                    <div className="ep-ws-card-actions">
                      {canRevise && allocationCanReviseQty(a) ? (
                        <button
                          type="button"
                          className="ep-btn sm"
                          disabled={busy}
                          title={`Ubah Qty · ${a.allocation_id}`}
                          onClick={() => openRevise(a, l)}
                        >
                          Ubah Qty
                        </button>
                      ) : canRevise ? (
                        <button
                          type="button"
                          className="ep-btn sm"
                          disabled
                          title="Alokasi tidak dapat direvisi"
                        >
                          Ubah Qty
                        </button>
                      ) : (
                        <span className="ep-muted">—</span>
                      )}
                    </div>
                  </WorkSheetCard>
                )),
              )}
            </div>
          ) : (
            <div className="ep-sched-alloc-scroll">
              <table
                className="ep-table ep-table-compact ep-sched-alloc-table"
                data-testid="sched-alloc-table"
              >
                <colgroup>
                  <col className="ep-sched-col-op" />
                  <col className="ep-sched-col-item" />
                  <col className="ep-sched-col-qty-sm" />
                  <col className="ep-sched-col-date" />
                  <col className="ep-sched-col-meta" />
                  <col className="ep-sched-col-meta" />
                  <col className="ep-sched-col-qty-sm" />
                  <col className="ep-sched-col-status" />
                  <col className="ep-sched-col-aksi" />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col">Operator</th>
                    <th scope="col">Item</th>
                    <th scope="col">
                      Qty
                      <br />
                      Alokasi
                    </th>
                    <th scope="col">
                      Tanggal
                      <br />
                      Pengerjaan
                    </th>
                    <th scope="col">
                      Warna
                      <br />
                      Cetak
                    </th>
                    <th scope="col">
                      Jenis
                      <br />
                      Screen
                    </th>
                    <th scope="col">
                      Qty
                      <br />
                      Cetak
                    </th>
                    <th scope="col">Status</th>
                    <th scope="col">Aksi</th>
                  </tr>
                </thead>
                <tbody>
                  {lines.flatMap((l) =>
                    l.allocations.map((a) => (
                      <tr
                        key={a.allocation_id}
                        title={`Allocation ID: ${a.allocation_id}`}
                      >
                        <td
                          className="ep-sched-td-text"
                          title={
                            (a.operator_display_name || "").trim() ||
                            "Nama belum tersedia"
                          }
                        >
                          {operatorLabel(a)}
                        </td>
                        <td className="ep-sched-td-text">
                          <span className="ep-sched-item-name">
                            {itemNameForLine(l)}
                          </span>
                          <span className="ep-sched-item-code">
                            {a.accurate_line_id}
                          </span>
                        </td>
                        <td className="ep-num ep-sched-td-qty">{qty(a.qty)}</td>
                        <td className="ep-sched-td-center">
                          {formatDateDisplay(a.schedule_date) || "—"}
                        </td>
                        <td className="ep-sched-td-center">
                          {a.print_color?.trim() || "—"}
                        </td>
                        <td className="ep-sched-td-center">
                          {a.screen_kind?.trim() || "—"}
                        </td>
                        <td className="ep-num ep-sched-td-qty">
                          {qty(a.qty_printed || 0)}
                        </td>
                        <td className="ep-sched-td-center">
                          <span className={allocStatusClass(a.status)}>
                            {allocStatusLabel(a.status)}
                          </span>
                        </td>
                        <td className="ep-sched-td-center">
                          {canRevise && allocationCanReviseQty(a) ? (
                            <button
                              type="button"
                              className="ep-btn sm"
                              disabled={busy}
                              title={`Ubah Qty · ${a.allocation_id}`}
                              onClick={() => openRevise(a, l)}
                            >
                              Ubah Qty
                            </button>
                          ) : canRevise ? (
                            <button
                              type="button"
                              className="ep-btn sm"
                              disabled
                              title="Alokasi tidak dapat direvisi"
                            >
                              Ubah Qty
                            </button>
                          ) : (
                            "—"
                          )}
                        </td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </div>
          )}

          {canAllocate && line && line.qty_unallocated > 0 ? (
            <>
              <p className="ep-sectionlabel">Tambah Alokasi</p>
              {operatorsLoading ? (
                <p
                  className="ep-notice ep-notice-compact"
                  data-testid="sched-op-loading"
                >
                  Memuat daftar operator…
                </p>
              ) : null}
              {operatorsError ? (
                <p className="ep-error" role="alert" data-testid="sched-op-blocker">
                  {operatorsError}
                </p>
              ) : null}
              <div
                className="ep-sched-form-grid"
                data-testid="sched-alloc-form"
              >
                <label className="ep-sched-field">
                  <span>Nama Barang</span>
                  <select
                    aria-label="Nama Barang"
                    value={lineId}
                    disabled={busy || lines.length === 0}
                    onChange={(e) => setLineId(e.target.value)}
                  >
                    {lines.map((l) => (
                      <option
                        key={l.accurate_line_id}
                        value={l.accurate_line_id}
                      >
                        {l.item_name || "Nama barang belum tersedia"}
                        {l.item_code || l.accurate_line_id
                          ? ` · ${[l.item_code ? `Kode ${l.item_code}` : null, `Line ${l.accurate_line_id}`]
                              .filter(Boolean)
                              .join(" · ")}`
                          : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="ep-sched-field">
                  <span>Operator</span>
                  <select
                    aria-label="Operator"
                    value={operatorEmail}
                    disabled={
                      busy || operatorsLoading || usableOperators.length === 0
                    }
                    onChange={(e) => setOperatorEmail(e.target.value)}
                  >
                    <option value="">
                      {operatorsLoading
                        ? "Memuat operator…"
                        : "Pilih operator…"}
                    </option>
                    {usableOperators.map((o) => (
                      <option key={o.id || o.email} value={o.email}>
                        {operatorCandidateLabel(o)}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="ep-sched-field">
                  <span>Qty Alokasi</span>
                  <input
                    aria-label="Qty alokasi"
                    type="number"
                    min={1}
                    value={allocQty}
                    disabled={busy}
                    onChange={(e) => setAllocQty(e.target.value)}
                  />
                </label>
                <label className="ep-sched-field">
                  <span>Tanggal Pengerjaan</span>
                  <input
                    aria-label="Tanggal pengerjaan"
                    type="date"
                    value={scheduleDate}
                    disabled={busy}
                    onChange={(e) => setScheduleDate(e.target.value)}
                  />
                </label>
                <label className="ep-sched-field">
                  <span>Warna Cetak</span>
                  <input
                    aria-label="Warna cetak"
                    placeholder="Contoh: Hitam"
                    value={printColor}
                    disabled={busy}
                    onChange={(e) => setPrintColor(e.target.value)}
                  />
                </label>
                <label className="ep-sched-field">
                  <span>Jenis Screen</span>
                  <select
                    aria-label="Jenis screen"
                    value={screenKind}
                    disabled={busy}
                    onChange={(e) =>
                      setScreenKind(e.target.value as ScreenKind | "")
                    }
                  >
                    <option value="">Pilih…</option>
                    <option value="Besar">Besar</option>
                    <option value="Kecil">Kecil</option>
                  </select>
                </label>
                <div className="ep-sched-form-actions">
                  <button
                    type="button"
                    className="ep-btn primary"
                    disabled={busy || formBlocked}
                    title={
                      line && !line.scheduling_unlocked
                        ? "Gate Design/Payment belum terpenuhi"
                        : operatorsError || usableOperators.length === 0
                          ? "Operator Master belum siap"
                          : "Simpan alokasi"
                    }
                    onClick={() => void onAllocate()}
                  >
                    {busy ? "Menyimpan…" : "Simpan Alokasi"}
                  </button>
                </div>
              </div>
            </>
          ) : null}
          {canAllocate && line && line.qty_unallocated <= 0 ? (
            <p className="ep-notice ep-notice-compact">
              Seluruh Qty sudah memiliki alokasi aktif.
            </p>
          ) : null}
          {!canAllocate && isReschedule ? (
            <p className="ep-notice ep-notice-compact">
              Penjadwalan ulang: ubah Qty alokasi melalui tombol Ubah Qty (revisi
              backend + alasan wajib).
            </p>
          ) : null}
        </>
      ) : null}

      {reviseTarget ? (
        <div
          className="ep-modalshade ep-modalshade-center ep-sched-revise-shade"
          data-testid="sched-revise-modal"
          onClick={(e) => {
            if (e.target === e.currentTarget) closeRevise();
          }}
        >
          <div
            className="ep-modal ep-sched-revise-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="sched-revise-title"
          >
            <header className="ep-modal-head">
              <div>
                <h2 id="sched-revise-title">Ubah Qty Alokasi</h2>
                <div className="ep-modal-submeta">
                  {reviseTarget.alloc.allocation_id}
                </div>
              </div>
              <button
                type="button"
                className="ep-btn sm"
                disabled={busy}
                onClick={closeRevise}
              >
                Tutup
              </button>
            </header>
            <div className="ep-modal-body">
              {reviseBounds ? (
                <>
                  <dl className="ep-kv ep-kv-sched ep-sched-revise-kv">
                    <dt>Operator</dt>
                    <dd>{operatorLabel(reviseTarget.alloc)}</dd>
                    <dt>Nama barang</dt>
                    <dd>{itemNameForLine(reviseTarget.line)}</dd>
                    <dt>Qty alokasi saat ini</dt>
                    <dd className="ep-num">{qty(reviseTarget.alloc.qty)}</dd>
                    <dt>Qty cetak terkonfirmasi</dt>
                    <dd className="ep-num">
                      {qty(reviseTarget.alloc.qty_printed || 0)}
                    </dd>
                    <dt>Qty minimum revisi</dt>
                    <dd className="ep-num">{qty(reviseBounds.min)}</dd>
                    <dt>Qty maksimum revisi</dt>
                    <dd className="ep-num">{qty(reviseBounds.max)}</dd>
                  </dl>
                  <div className="ep-sched-revise-fields">
                    <label className="ep-sched-field">
                      <span>Qty baru</span>
                      <input
                        type="number"
                        inputMode="numeric"
                        min={reviseBounds.min}
                        max={reviseBounds.max}
                        step={1}
                        aria-label="Qty baru"
                        className="ep-num"
                        value={reviseQty}
                        disabled={busy}
                        onChange={(e) => setReviseQty(e.target.value)}
                      />
                    </label>
                    <label className="ep-sched-field">
                      <span>Alasan revisi</span>
                      <input
                        type="text"
                        aria-label="Alasan revisi"
                        value={reviseReason}
                        disabled={busy}
                        onChange={(e) => setReviseReason(e.target.value)}
                      />
                    </label>
                  </div>
                  {reviseError ? (
                    <p className="ep-error" role="alert">
                      {reviseError}
                    </p>
                  ) : null}
                </>
              ) : null}
            </div>
            <footer className="ep-modal-foot">
              <div className="ep-modal-foot-actions">
                <button
                  type="button"
                  className="ep-btn"
                  disabled={busy}
                  onClick={closeRevise}
                >
                  Batal
                </button>
                <button
                  type="button"
                  className="ep-btn primary"
                  disabled={busy}
                  onClick={() => void submitRevise()}
                >
                  {busy ? "Menyimpan…" : "Simpan Revisi"}
                </button>
              </div>
            </footer>
          </div>
        </div>
      ) : null}

      {designOpen ? (
        <div
          className="ep-modalshade ep-modalshade-center ep-sched-design-shade"
          data-testid="sched-design-viewer"
          onClick={(e) => {
            if (e.target === e.currentTarget) setDesignOpen(false);
          }}
        >
          <div className="ep-modal ep-sched-design-modal" role="dialog" aria-modal="true">
            <header className="ep-modal-head">
              <div>
                <h2>Desain PO</h2>
                <div className="ep-modal-submeta">
                  {task.ref.po_no || poId}
                  {latest ? ` · v${latest.version}` : ""}
                </div>
              </div>
              <button
                type="button"
                className="ep-btn sm"
                onClick={() => setDesignOpen(false)}
              >
                Tutup
              </button>
            </header>
            <div className="ep-modal-body">
              {designLoading ? (
                <p className="ep-notice">Memuat desain…</p>
              ) : null}
              {designError ? (
                <p className="ep-error" role="alert">
                  {designError}
                </p>
              ) : null}
              {!designLoading && !designError && !latest ? (
                <p className="ep-empty">Belum ada file desain untuk PO ini.</p>
              ) : null}
              {latest ? (
                <>
                  <dl className="ep-kv ep-kv-sched">
                    <dt>Versi</dt>
                    <dd>{latest.version}</dd>
                    <dt>File</dt>
                    <dd>{latest.filename || "—"}</dd>
                    <dt>Status</dt>
                    <dd>
                      {latest.confirmed_at
                        ? "Terkonfirmasi Designer"
                        : "Draft / menunggu konfirmasi"}
                    </dd>
                    <dt>Approval customer</dt>
                    <dd>
                      {designState?.approval
                        ? `Disetujui${
                            designState.approval.signer_name
                              ? ` · ${designState.approval.signer_name}`
                              : ""
                          }`
                        : "Belum ada"}
                    </dd>
                  </dl>
                  {contentUrl && designContentOk ? (
                    <div className="ep-sched-design-frame">
                      {isPdf ? (
                        <iframe
                          title="Preview desain PDF"
                          src={contentUrl}
                          className="ep-sched-design-embed"
                        />
                      ) : (
                        <img
                          src={contentUrl}
                          alt={latest.filename || "Desain"}
                          className="ep-sched-design-img"
                        />
                      )}
                    </div>
                  ) : (
                    <p className="ep-empty" data-testid="sched-design-empty-file">
                      File bukti desain belum tersedia untuk dipreview.
                    </p>
                  )}
                </>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

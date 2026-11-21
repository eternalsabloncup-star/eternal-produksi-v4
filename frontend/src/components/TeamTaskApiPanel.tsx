import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  confirmCoordination,
  confirmDelivered,
  confirmDeliveryFailed,
  confirmLoadingMatch,
  deliveryEvidenceContentUrl,
  fetchDeliveryDetail,
  fetchLoadingValidation,
  rejectLoadingMismatch,
  type DeliveryDetail,
  type LoadingValidation,
} from "../api/delivery";
import {
  confirmProductionQty,
  confirmSampleSubmission,
  decideSample,
  fetchRoleActions,
  fetchSamples,
  fetchTaskConfirmations,
  fetchTasks,
  newIdempotencyKey,
  sampleEvidenceContentUrl,
  uploadSample,
  type OperationalRole,
  type OperationalTask,
  type ProductionSample,
  type TaskAction,
  type TaskConfirmation,
} from "../api/operational";
import {
  fetchPoDetail,
  urgencyLabel,
  type PoDetailSummary,
} from "../api/poDetail";
import { useAuth } from "../auth";
import { formatDateDisplay, formatDateTimeDisplay } from "../lib/formatDate";
import { formatPwaBranchLabel } from "../mobile/branchDisplay";
import { useMobileOpsActive } from "../mobile/MobileOpsContext";
import { hasViewAllTasks, isSuperAdmin } from "../roles";
import { ConfirmationSuccessOverlay } from "./ConfirmationSuccessOverlay";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";
import { DesignWorkflowPanel } from "./DesignWorkflowPanel";
import { EvidenceContentPreview } from "./EvidenceContentPreview";
import { TeamTaskSchedulingForm } from "./TeamTaskSchedulingForm";
import {
  allowTaskAction,
  aggregateStatusCounts,
  canShowQtyConfirmForm,
  filterTasksByStatus,
  filterTasksForActor,
  formatCountBadge,
  isCoordinationTask,
  isLoadingSjTask,
  isSampleTask,
  isSchedulingTask,
  remainingQty,
  latestDraftSample,
  sampleStatusClass,
  sampleStatusLabel,
  screenAllocQty,
  shortfallLabel,
  statusBadgeClass,
  statusLabel,
  taskDisplayLabel,
  taskItemDisplayName,
  taskItemSecondaryMeta,
  TASK_LIST_FETCH_LIMIT,
  type StatusFilter,
} from "./teamTaskUi";

type Props = {
  /** Null + allowAllRoles = view_all tanpa filter role (bukan impersonation). */
  role: OperationalRole | null;
  roleLabel: string;
  /**
   * Filter status awal. Desktop default ``all``; PWA Tugas Saya memakai ``belum``.
   * State lokal tetap dipertahankan selama panel hidup (navigasi tab PWA).
   */
  initialStatusFilter?: StatusFilter;
  /** Sembunyikan aksi mutation (Holding view-only). Backend tetap menolak. */
  readOnly?: boolean;
  /** Fetch tanpa query role — hanya untuk akun view_all yang sah. */
  allowAllRoles?: boolean;
};

const SAMPLE_UPLOAD_TASK = "konfirmasi-sample";
const SAMPLE_APPROVAL_TASK = "approval-sampel";
const DRIVER_TASK = "pengiriman";
const DESIGN_TASK = "design";

function formatQty(n: number | undefined): string {
  if (n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString("id-ID");
}

function signatureBlob(canvas: HTMLCanvasElement): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
}

/** Tugas peran dari task engine MongoDB V4 — tanpa fixture DEMO. */
export function TeamTaskApiPanel({
  role,
  roleLabel,
  initialStatusFilter = "all",
  readOnly = false,
  allowAllRoles = false,
}: Props) {
  const { user, isSuperAdmin: authSuper } = useAuth();
  const elevated = authSuper || isSuperAdmin(user?.role);
  const pwaOps = useMobileOpsActive();

  const [allTasks, setAllTasks] = useState<OperationalTask[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [statusFilter, setStatusFilter] =
    useState<StatusFilter>(initialStatusFilter);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<OperationalTask | null>(null);
  const [history, setHistory] = useState<TaskConfirmation[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [qtyInput, setQtyInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const [sampleRows, setSampleRows] = useState<ProductionSample[]>([]);
  const [rejectReason, setRejectReason] = useState("");
  const fileRef = useRef<HTMLInputElement | null>(null);
  const photosRef = useRef<HTMLInputElement | null>(null);
  const signatureRef = useRef<HTMLCanvasElement | null>(null);
  const drawingRef = useRef(false);
  const [recipientName, setRecipientName] = useState("");
  const [failureReason, setFailureReason] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);
  const [grantsByTaskType, setGrantsByTaskType] = useState<Map<
    string,
    TaskAction[]
  > | null>(null);
  const [grantsError, setGrantsError] = useState<string | null>(null);
  const [deliveryDetail, setDeliveryDetail] = useState<DeliveryDetail | null>(
    null,
  );
  const [deliveryDetailError, setDeliveryDetailError] = useState<string | null>(
    null,
  );
  const [loadingValidation, setLoadingValidation] =
    useState<LoadingValidation | null>(null);
  const [loadingValidationError, setLoadingValidationError] = useState<
    string | null
  >(null);
  const [successOpen, setSuccessOpen] = useState(false);
  const [successTone, setSuccessTone] = useState<"success" | "error">("success");
  const [successTitle, setSuccessTitle] = useState("Berhasil");
  const [successMsg, setSuccessMsg] = useState("");
  const [evidencePreview, setEvidencePreview] = useState<{
    title: string;
    url: string;
    filename?: string | null;
  } | null>(null);
  const [poDetail, setPoDetail] = useState<PoDetailSummary | null>(null);
  const [poDetailError, setPoDetailError] = useState<string | null>(null);
  const [poDetailLoading, setPoDetailLoading] = useState(false);
  const [headerReloading, setHeaderReloading] = useState(false);
  const designReloadRef = useRef<(() => Promise<void>) | null>(null);
  const registerDesignReload = useCallback((fn: () => Promise<void>) => {
    designReloadRef.current = fn;
  }, []);
  const schedDesignOpenRef = useRef<(() => void) | null>(null);
  const registerSchedDesignOpen = useCallback((fn: (() => void) | null) => {
    schedDesignOpenRef.current = fn;
  }, []);

  const canAct = useCallback(
    (taskType: string, action: TaskAction) => {
      if (readOnly) return false;
      return allowTaskAction({
        isSuperAdmin: elevated,
        grantsByTaskType,
        taskType,
        action,
      });
    },
    [elevated, grantsByTaskType, readOnly],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchTasks({
        role: allowAllRoles ? undefined : role ?? undefined,
        limit: TASK_LIST_FETCH_LIMIT,
      });
      setAllTasks(res.items);
      setTruncated(res.items.length >= TASK_LIST_FETCH_LIMIT);
    } catch (err) {
      setAllTasks([]);
      setTruncated(false);
      setError(err instanceof Error ? err.message : "Gagal memuat tugas.");
    } finally {
      setLoading(false);
    }
  }, [role, allowAllRoles]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (readOnly || !role) {
        setGrantsByTaskType(new Map());
        setGrantsError(null);
        return;
      }
      try {
        const res = await fetchRoleActions(role);
        if (cancelled) return;
        const map = new Map<string, TaskAction[]>();
        for (const g of res.items) {
          if (g.role === role) map.set(g.task_type, g.actions);
        }
        setGrantsByTaskType(map);
        setGrantsError(null);
      } catch (err) {
        if (cancelled) return;
        setGrantsByTaskType(null);
        setGrantsError(
          err instanceof Error
            ? err.message
            : "Izin Master tidak dimuat — aksi mutasi dinonaktifkan sampai izin tersedia.",
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [role, readOnly]);

  const viewAllTasks = hasViewAllTasks(user);

  const scopedTasks = useMemo(
    () =>
      filterTasksForActor(allTasks, {
        isSuperAdmin: elevated,
        actorEmail: user?.email,
        operationalRole: role,
        // Selaras backend loading_sj_visible_to_actor — jangan drop Loading SJ
        // yang sudah diizinkan oleh operations.tasks.view_all.
        viewAllTasks,
      }),
    [allTasks, elevated, user?.email, role, viewAllTasks],
  );

  const statusCounts = useMemo(
    () => aggregateStatusCounts(scopedTasks),
    [scopedTasks],
  );

  const statusFiltered = useMemo(
    () => filterTasksByStatus(scopedTasks, statusFilter),
    [scopedTasks, statusFilter],
  );

  const filteredTasks = useMemo(() => {
    const qq = q.trim().toLowerCase();
    if (!qq) return statusFiltered;
    return statusFiltered.filter((t) => {
      const hay = [
        taskDisplayLabel(t),
        t.task_type,
        t.ref.po_no,
        t.ref.sj_no,
        taskItemDisplayName(t),
        t.ref.item_code,
        t.ref.accurate_line_id,
        t.ref.allocation_id,
        statusLabel(t.status),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(qq);
    });
  }, [statusFiltered, q]);

  const pageTasks = useMemo(() => {
    const start = (page - 1) * pageSize;
    return filteredTasks.slice(start, start + pageSize);
  }, [filteredTasks, page, pageSize]);

  const loadPoDetailForTask = useCallback(async (task: OperationalTask) => {
    const poKey = task.ref.accurate_po_id || task.ref.po_no;
    // Driver berorientasi SJ — PO detail opsional; role lain butuh customer/urgent.
    const needsPoDetail =
      Boolean(poKey) && task.task_type !== DRIVER_TASK;
    if (!needsPoDetail || !poKey) {
      setPoDetail(null);
      setPoDetailError(null);
      setPoDetailLoading(false);
      return;
    }
    setPoDetailLoading(true);
    setPoDetailError(null);
    try {
      setPoDetail(await fetchPoDetail(poKey));
    } catch (err) {
      setPoDetail(null);
      setPoDetailError(
        err instanceof Error ? err.message : "Gagal memuat detail PO.",
      );
    } finally {
      setPoDetailLoading(false);
    }
  }, []);

  const openTask = useCallback(async (task: OperationalTask) => {
    setSelected(task);
    setQtyInput("");
    setRejectReason("");
    setActionError(null);
    setActionNotice(null);
    setEvidencePreview(null);
    setHistoryError(null);
    setDeliveryDetail(null);
    setDeliveryDetailError(null);
    setLoadingValidation(null);
    setLoadingValidationError(null);
    setPoDetail(null);
    setPoDetailError(null);
    try {
      const res = await fetchTaskConfirmations(task.task_key);
      setHistory(res.items);
    } catch (err) {
      setHistory([]);
      setHistoryError(
        err instanceof Error ? err.message : "Riwayat konfirmasi gagal dimuat.",
      );
    }
    const allocationId = task.ref.allocation_id;
    if (allocationId && isSampleTask(task.task_type)) {
      try {
        const res = await fetchSamples(allocationId);
        setSampleRows(res.items);
      } catch {
        setSampleRows([]);
      }
    } else {
      setSampleRows([]);
    }
    if (task.task_type === DRIVER_TASK && task.ref.accurate_delivery_note_id) {
      try {
        const detail = await fetchDeliveryDetail(
          task.ref.accurate_delivery_note_id,
        );
        setDeliveryDetail(detail);
      } catch (err) {
        setDeliveryDetailError(
          err instanceof Error
            ? err.message
            : "Detail pengiriman/bukti gagal dimuat.",
        );
      }
    }
    if (
      isLoadingSjTask(task.task_type) &&
      task.ref.accurate_delivery_note_id
    ) {
      try {
        setLoadingValidation(
          await fetchLoadingValidation(task.ref.accurate_delivery_note_id),
        );
      } catch (err) {
        setLoadingValidationError(
          err instanceof Error
            ? err.message
            : "Validasi Loading gagal dimuat.",
        );
      }
    }
    await loadPoDetailForTask(task);
  }, [loadPoDetailForTask]);

  async function onDesignerHeaderReload() {
    if (!selected || selected.task_type !== DESIGN_TASK) return;
    setHeaderReloading(true);
    setActionError(null);
    try {
      await Promise.all([
        loadPoDetailForTask(selected),
        designReloadRef.current?.() ?? Promise.resolve(),
        fetchTaskConfirmations(selected.task_key)
          .then((res) => {
            setHistory(res.items);
            setHistoryError(null);
          })
          .catch((err) => {
            setHistoryError(
              err instanceof Error
                ? err.message
                : "Riwayat konfirmasi gagal dimuat.",
            );
          }),
      ]);
      setActionNotice("Data desain dan PO dimuat ulang.");
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : "Gagal memuat ulang.",
      );
    } finally {
      setHeaderReloading(false);
    }
  }

  const columns: ColumnDef<OperationalTask>[] = useMemo(
    () => {
      const base: ColumnDef<OperationalTask>[] = [
      {
        id: "taskType",
        label: "Jenis Pekerjaan",
        hideable: true,
        filterType: "text",
        defaultWidth: 170,
        getFilterValue: (t) => taskDisplayLabel(t),
        render: (t) => (
          <span className={t.coordination ? "ep-text-danger" : undefined}>
            {taskDisplayLabel(t)}
          </span>
        ),
      },
      {
        id: "poSj",
        label: "No. PO / SJ",
        sticky: "po",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (t) => t.ref.po_no || t.ref.sj_no || "",
        render: (t) => t.ref.po_no || t.ref.sj_no || "—",
      },
      {
        id: "outletName",
        label: "Nama Customer",
        sticky: "customer",
        hideable: true,
        filterType: "text",
        defaultWidth: 160,
        getFilterValue: (t) => t.outlet_name || "",
        render: (t) => t.outlet_name?.trim() || "—",
      },
      ];
      if (role === "production-head") {
        base.push({
          id: "shipDates",
          label: "Tanggal Pengiriman",
          hideable: true,
          filterType: "date",
          defaultWidth: 150,
          getFilterValue: (t) => (t.ship_dates || []).join(" "),
          getSortValue: (t) => (t.ship_dates && t.ship_dates[0]) || "",
          render: (t) => {
            const dates = t.ship_dates || [];
            if (!dates.length) return "—";
            const text = dates
              .map((d) => formatDateDisplay(d, ""))
              .filter(Boolean)
              .join(", ");
            return (
              <span title={text || undefined}>{text || "—"}</span>
            );
          },
        });
      }
      base.push(
      {
        id: "itemName",
        label: "Nama Barang",
        hideable: true,
        filterType: "text",
        defaultWidth: 200,
        getFilterValue: (t) => {
          const name = taskItemDisplayName(t);
          const meta = taskItemSecondaryMeta(t);
          return meta ? `${name} ${meta}` : name;
        },
        getSortValue: (t) => taskItemDisplayName(t),
        render: (t) => {
          const name = taskItemDisplayName(t);
          const meta = taskItemSecondaryMeta(t);
          return (
            <span className="ep-sched-td-text" data-testid="task-item-name-cell">
              <span className="ep-sched-item-name">{name}</span>
              {meta ? (
                <span className="ep-sched-item-code">{meta}</span>
              ) : null}
            </span>
          );
        },
      },
      {
        id: "qtyTarget",
        label: "Qty Target",
        hideable: true,
        filterType: "number",
        defaultWidth: 100,
        getFilterValue: (t) => {
          if (t.qty_target !== undefined) return String(t.qty_target);
          const alloc = screenAllocQty(t);
          return alloc === null ? "" : String(alloc);
        },
        getSortValue: (t) =>
          t.qty_target ?? screenAllocQty(t) ?? null,
        render: (t) => {
          if (t.qty_target !== undefined) {
            return (
              <span className="ep-num">{formatQty(t.qty_target)}</span>
            );
          }
          const alloc = screenAllocQty(t);
          if (alloc !== null) {
            return (
              <span
                className="ep-num"
                title="Qty Alokasi (info) — Screen tidak dibatasi jadwal"
                data-testid="screen-qty-alokasi"
              >
                {formatQty(alloc)}
                <span className="ep-sched-item-code">alokasi</span>
              </span>
            );
          }
          return <span className="ep-num">—</span>;
        },
      },
      {
        id: "qtyConfirmed",
        label: "Qty Terkonfirmasi",
        hideable: true,
        filterType: "number",
        defaultWidth: 120,
        getFilterValue: (t) => String(t.qty_confirmed),
        getSortValue: (t) => t.qty_confirmed,
        render: (t) => (
          <span className="ep-num">{formatQty(t.qty_confirmed)}</span>
        ),
      },
      {
        id: "shortfall",
        label: "Selisih Qty",
        hideable: true,
        filterType: "number",
        defaultWidth: 110,
        getFilterValue: (t) => {
          const r = remainingQty(t);
          return r === null ? "" : String(r);
        },
        getSortValue: (t) => remainingQty(t),
        render: (t) => {
          const r = remainingQty(t);
          return (
            <span className="ep-num">{r === null ? "—" : formatQty(r)}</span>
          );
        },
      },
      {
        id: "status",
        label: "Status",
        hideable: true,
        filterType: "text",
        defaultWidth: 130,
        getFilterValue: (t) => statusLabel(t.status),
        render: (t) => (
          <span className={statusBadgeClass(t.status)}>
            {statusLabel(t.status)}
          </span>
        ),
      },
      {
        id: "action",
        label: "Aksi",
        hideable: false,
        filterType: "text",
        defaultWidth: 90,
        getFilterValue: () => "",
        render: (t) => (
          <button
            type="button"
            className="ep-btn sm"
            onClick={(e) => {
              e.stopPropagation();
              void openTask(t);
            }}
          >
            Buka
          </button>
        ),
      },
      );
      return base;
    },
    [openTask, role],
  );

  async function reloadAfterAction(task: OperationalTask) {
    const [listed, hist] = await Promise.all([
      fetchTasks({
        role: allowAllRoles ? undefined : role ?? undefined,
        limit: TASK_LIST_FETCH_LIMIT,
      }),
      fetchTaskConfirmations(task.task_key).catch(() => ({
        items: [] as TaskConfirmation[],
      })),
    ]);
    setAllTasks(listed.items);
    setTruncated(listed.items.length >= TASK_LIST_FETCH_LIMIT);
    setHistory(hist.items);
    setHistoryError(null);
    const fresh =
      listed.items.find((t) => t.task_key === task.task_key) || task;
    setSelected(fresh);
    if (task.ref.allocation_id && isSampleTask(task.task_type)) {
      const res = await fetchSamples(task.ref.allocation_id).catch(() => ({
        items: [] as ProductionSample[],
      }));
      setSampleRows(res.items);
    }
    if (task.task_type === DRIVER_TASK && task.ref.accurate_delivery_note_id) {
      try {
        setDeliveryDetail(
          await fetchDeliveryDetail(task.ref.accurate_delivery_note_id),
        );
        setDeliveryDetailError(null);
      } catch {
        /* keep previous */
      }
    }
  }

  function showSuccess(message: string) {
    setSuccessTone("success");
    setSuccessTitle("Berhasil");
    setSuccessMsg(message);
    setSuccessOpen(true);
    window.dispatchEvent(new CustomEvent("eternal:tasks-changed"));
  }

  function showActionFailure(message: string) {
    setActionError(message);
    setSuccessTone("error");
    setSuccessTitle("Gagal");
    setSuccessMsg(message);
    setSuccessOpen(true);
  }

  async function onUploadSampleDraft() {
    const file = fileRef.current?.files?.[0];
    if (!selected?.ref.allocation_id || !file) {
      setActionError("Pilih file sampel terlebih dahulu.");
      return;
    }
    if (!canAct(SAMPLE_UPLOAD_TASK, "input") && !canAct(SAMPLE_UPLOAD_TASK, "confirm")) {
      setActionError("Aksi upload sampel tidak diizinkan untuk peran ini.");
      return;
    }
    setBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const res = await uploadSample(selected.ref.allocation_id, file);
      const msg = `Draft sampel v${res.sample.version} tersimpan — belum diajukan.`;
      setActionNotice(msg);
      showSuccess(msg);
      if (fileRef.current) fileRef.current.value = "";
      await reloadAfterAction(selected);
    } catch (err) {
      showActionFailure(
        err instanceof Error ? err.message : "Upload draft sampel gagal.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function onConfirmSampleSubmission() {
    if (!selected?.ref.allocation_id) return;
    if (!canAct(SAMPLE_UPLOAD_TASK, "confirm") && !canAct(SAMPLE_UPLOAD_TASK, "input")) {
      setActionError("Aksi konfirmasi sampel tidak diizinkan untuk peran ini.");
      return;
    }
    const draft = latestDraftSample(sampleRows);
    if (!draft) {
      setActionError("Belum ada draft aktif — unggah file terlebih dahulu.");
      return;
    }
    setBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const res = await confirmSampleSubmission(selected.ref.allocation_id);
      const msg = res.duplicate
        ? `Sampel v${res.sample.version} sudah menunggu approval.`
        : `Sampel v${res.sample.version} diajukan — menunggu Approval Kepala Produksi.`;
      setActionNotice(msg);
      showSuccess(msg);
      await reloadAfterAction(selected);
    } catch (err) {
      showActionFailure(
        err instanceof Error ? err.message : "Konfirmasi sampel gagal.",
      );
    } finally {
      setBusy(false);
    }
  }

  function signaturePos(e: React.PointerEvent<HTMLCanvasElement>) {
    const rect = signatureRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  function startSignature(e: React.PointerEvent<HTMLCanvasElement>) {
    const ctx = signatureRef.current?.getContext("2d");
    if (!ctx) return;
    drawingRef.current = true;
    const { x, y } = signaturePos(e);
    ctx.beginPath();
    ctx.moveTo(x, y);
  }

  function moveSignature(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!drawingRef.current) return;
    const ctx = signatureRef.current?.getContext("2d");
    if (!ctx) return;
    const { x, y } = signaturePos(e);
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.strokeStyle = "#17341f";
    ctx.lineTo(x, y);
    ctx.stroke();
  }

  function endSignature() {
    drawingRef.current = false;
  }

  async function onDeliverySuccess() {
    const sjId = selected?.ref.accurate_delivery_note_id;
    const files = Array.from(photosRef.current?.files || []);
    const canvas = signatureRef.current;
    if (!sjId || !canvas) return;
    if (!canAct(DRIVER_TASK, "confirm")) {
      setActionError("Konfirmasi pengiriman tidak diizinkan untuk peran ini.");
      return;
    }
    if (files.length < 2) {
      setActionError("Konfirmasi pengiriman memerlukan minimal 2 foto.");
      return;
    }
    if (!recipientName.trim()) {
      setActionError("Nama penerima wajib diisi.");
      return;
    }
    const signature = await signatureBlob(canvas);
    if (!signature) {
      setActionError("Tanda tangan penerima wajib diisi.");
      return;
    }
    setBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const res = await confirmDelivered(
        sjId,
        recipientName.trim(),
        files,
        signature,
      );
      const msg = `Pengiriman ${res.status} · ${res.photo_evidence_ids.length} foto tersimpan.`;
      setActionNotice(msg);
      showSuccess(msg);
      if (photosRef.current) photosRef.current.value = "";
      setRecipientName("");
      await reloadAfterAction(selected!);
    } catch (err) {
      showActionFailure(
        err instanceof Error ? err.message : "Konfirmasi pengiriman gagal.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function onDeliveryFailed() {
    const sjId = selected?.ref.accurate_delivery_note_id;
    if (!sjId) return;
    if (!canAct(DRIVER_TASK, "confirm")) {
      setActionError("Menandai gagal tidak diizinkan untuk peran ini.");
      return;
    }
    if (!failureReason.trim()) {
      setActionError("Alasan pengiriman gagal wajib diisi.");
      return;
    }
    setBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      await confirmDeliveryFailed(sjId, failureReason.trim());
      const msg =
        "Pengiriman ditandai gagal; tugas Koordinasi Pengiriman Gagal dibuat.";
      setActionNotice(msg);
      showSuccess(msg);
      setFailureReason("");
      await reloadAfterAction(selected!);
    } catch (err) {
      showActionFailure(
        err instanceof Error ? err.message : "Gagal menyimpan kegagalan.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function onLoadingMatch() {
    if (!selected?.ref.accurate_delivery_note_id) return;
    if (!canAct("loading-sj", "confirm") && !canAct(DRIVER_TASK, "confirm")) {
      setActionError("Konfirmasi Loading tidak diizinkan.");
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      const res = await confirmLoadingMatch(
        selected.ref.accurate_delivery_note_id,
        newIdempotencyKey(`loading-match:${selected.task_key}`),
      );
      const msg = res.duplicate
        ? "Loading sudah tercatat sebelumnya."
        : "Loading Sesuai — Qty Packing dikunci untuk SJ ini.";
      showSuccess(msg);
      await reloadAfterAction(selected);
    } catch (err) {
      showActionFailure(
        err instanceof Error ? err.message : "Konfirmasi Loading gagal.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function onLoadingMismatch() {
    if (!selected?.ref.accurate_delivery_note_id) return;
    if (!canAct("loading-sj", "confirm") && !canAct(DRIVER_TASK, "confirm")) {
      setActionError("Menandai Tidak Sesuai tidak diizinkan.");
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      await rejectLoadingMismatch(
        selected.ref.accurate_delivery_note_id,
        "Petugas menandai Tidak Sesuai dari Tugas Tim.",
      );
      showSuccess("Tidak Sesuai — tugas koordinasi Kepala Produksi dibuat.");
      await reloadAfterAction(selected);
    } catch (err) {
      showActionFailure(
        err instanceof Error ? err.message : "Gagal menandai Tidak Sesuai.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function onConfirmCoordination() {
    if (!selected) return;
    if (!canAct(selected.task_type, "confirm")) {
      setActionError("Konfirmasi koordinasi tidak diizinkan untuk peran ini.");
      return;
    }
    setBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      await confirmCoordination(
        selected.task_key,
        newIdempotencyKey(`coord:${selected.task_key}`),
      );
      const msg = "Koordinasi administrasi ditandai selesai.";
      setActionNotice(msg);
      showSuccess(msg);
      await reloadAfterAction(selected);
    } catch (err) {
      showActionFailure(
        err instanceof Error ? err.message : "Konfirmasi koordinasi gagal.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function onDecideSample(version: number, approved: boolean) {
    if (!selected?.ref.allocation_id) return;
    if (!canAct(SAMPLE_APPROVAL_TASK, "approve")) {
      setActionError("Keputusan sampel tidak diizinkan untuk peran ini.");
      return;
    }
    if (!approved && !rejectReason.trim()) {
      setActionError("Alasan penolakan wajib diisi.");
      return;
    }
    setBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      await decideSample(
        selected.ref.allocation_id,
        version,
        approved,
        approved ? undefined : rejectReason.trim(),
      );
      const msg = approved
        ? `Sampel versi ${version} disetujui — cetak massal terbuka.`
        : `Sampel versi ${version} ditolak.`;
      setActionNotice(msg);
      showSuccess(msg);
      setRejectReason("");
      await reloadAfterAction(selected);
    } catch (err) {
      showActionFailure(
        err instanceof Error ? err.message : "Keputusan sampel gagal.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function onConfirm() {
    if (!selected) return;
    if (isSchedulingTask(selected.task_type)) {
      setActionError(
        "Penjadwalan tidak memakai konfirmasi Qty generik. Gunakan form alokasi.",
      );
      return;
    }
    if (!canAct(selected.task_type, "confirm") && !canAct(selected.task_type, "input")) {
      setActionError("Konfirmasi Qty tidak diizinkan untuk peran ini.");
      return;
    }
    const qty = Number(qtyInput);
    if (!Number.isFinite(qty) || qty <= 0) {
      setActionError("Qty konfirmasi harus lebih besar dari nol.");
      return;
    }
    const rem = remainingQty(selected);
    if (rem !== null && qty > rem) {
      setActionError(
        `Qty melebihi sisa yang tersedia (${formatQty(rem)}).`,
      );
      return;
    }
    setBusy(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const res = await confirmProductionQty(
        selected,
        qty,
        newIdempotencyKey(`${role}:${selected.task_key}`),
      );
      const msg = res.duplicate
        ? "Konfirmasi sama sudah tercatat — Qty tidak digandakan."
        : `Qty ${formatQty(qty)} tersimpan di MongoDB V4.`;
      setActionNotice(msg);
      showSuccess(msg);
      setSelected(res.task);
      setQtyInput("");
      await reloadAfterAction(res.task);
    } catch (err) {
      showActionFailure(err instanceof Error ? err.message : "Konfirmasi gagal.");
    } finally {
      setBusy(false);
    }
  }

  function setFilter(next: StatusFilter) {
    setStatusFilter(next);
    setPage(1);
  }

  const selectedIsSample =
    selected != null && isSampleTask(selected.task_type);
  const selectedIsDesigner =
    selected != null && selected.task_type === DESIGN_TASK;
  const selectedIsScheduling =
    selected != null && isSchedulingTask(selected.task_type);
  const selectedIsDriver =
    selected != null && selected.task_type === DRIVER_TASK;

  return (
    <div className="ep-taskpanel" data-testid="team-task-api-panel">
      {error ? (
        <div className="ep-error" role="alert">
          {error}
            <button
              type="button"
              className="ep-btn sm"
              onClick={() => {
                void load();
                window.dispatchEvent(new CustomEvent("eternal:tasks-changed"));
              }}
            >
              Coba Lagi
            </button>
        </div>
      ) : null}
      {truncated ? (
        <p className="ep-notice" data-testid="tasks-truncated-notice" role="status">
          Daftar tugas mencapai batas baca API ({TASK_LIST_FETCH_LIMIT}). Angka
          filter adalah batas bawah — bukan total lengkap.
        </p>
      ) : null}
      {grantsError ? (
        <p className="ep-notice" role="status">
          {grantsError}
        </p>
      ) : null}

      <DataTable
        tableId={`po-accurate:tasks-${role ?? "all"}`}
        columns={columns}
        rows={loading ? [] : pageTasks}
        filterSourceRows={filteredTasks}
        rowKey={(t) => t.task_key}
        enableColumnManager
        inlineColumnTools
        page={page}
        pageSize={pageSize}
        total={loading ? 0 : filteredTasks.length}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        onRowActivate={(t) => void openTask(t)}
        emptyMessage={
          loading
            ? `Memuat tugas ${roleLabel} dari MongoDB V4…`
            : `Belum ada tugas ${roleLabel} di MongoDB V4.`
        }
        filterSlot={(tools) => (
          <div className="ep-filterbar is-compact">
            <strong>{roleLabel}</strong>
            <button
              type="button"
              className={
                statusFilter === "all" ? "ep-btn sm is-active" : "ep-btn sm"
              }
              onClick={() => setFilter("all")}
            >
              Semua ({formatCountBadge(statusCounts.all, truncated)})
            </button>
            <button
              type="button"
              className={
                statusFilter === "belum" ? "ep-btn sm is-active" : "ep-btn sm"
              }
              onClick={() => setFilter("belum")}
            >
              Belum Dikerjakan ({formatCountBadge(statusCounts.belum, truncated)})
            </button>
            <button
              type="button"
              className={
                statusFilter === "sebagian"
                  ? "ep-btn sm is-active"
                  : "ep-btn sm"
              }
              onClick={() => setFilter("sebagian")}
            >
              Sebagian ({formatCountBadge(statusCounts.sebagian, truncated)})
            </button>
            <button
              type="button"
              className={
                statusFilter === "selesai"
                  ? "ep-btn sm is-active"
                  : "ep-btn sm"
              }
              onClick={() => setFilter("selesai")}
            >
              Selesai ({formatCountBadge(statusCounts.selesai, truncated)})
            </button>
            <div className="ep-search">
              <span style={{ fontSize: 14, color: "#97a8bc" }} aria-hidden>
                ⌕
              </span>
              <input
                placeholder="Cari PO / SJ / jenis…"
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setPage(1);
                }}
                aria-label="Cari tugas"
              />
            </div>
            <button
              type="button"
              className="ep-btn sm"
              onClick={() => {
                setQ("");
                setStatusFilter("all");
                setPage(1);
              }}
            >
              Reset filter
            </button>
            <button
              type="button"
              className="ep-btn sm"
              onClick={() => {
                void load();
                window.dispatchEvent(new CustomEvent("eternal:tasks-changed"));
              }}
            >
              Muat ulang
            </button>
            <button
              type="button"
              className="ep-btn sm"
              onClick={(e) => tools.openColumns(e.currentTarget)}
            >
              ▥ Kolom Tabel
            </button>
            <button
              type="button"
              className="ep-btn sm"
              onClick={() => void tools.resetPrefs()}
            >
              Reset tampilan
            </button>
            {tools.prefsHint ? (
              <span
                className={`ep-prefs-hint${tools.saveState === "error" ? " is-error" : ""}`}
                role={tools.saveState === "error" ? "alert" : undefined}
              >
                {tools.prefsHint}
              </span>
            ) : null}
          </div>
        )}
      />

      {selected ? (
        <div
          className="ep-modalshade ep-modalshade-center"
          onClick={(e) => {
            if (e.target === e.currentTarget) setSelected(null);
          }}
        >
          <div
            className={`ep-modal ep-task-modal${
              selectedIsDesigner ? " is-designer" : ""
            }${selectedIsScheduling ? " is-scheduling" : ""}`}
            role="dialog"
            aria-modal="true"
            data-testid="team-task-modal"
            data-task-type={selected.task_type}
          >
            <header className="ep-modal-head">
              <div className="ep-modal-head-main">
                <div className="ep-modal-kicker">{roleLabel}</div>
                <h2 data-testid="task-modal-title">
                  {taskDisplayLabel(selected)}
                </h2>
                {!selectedIsDriver ? (
                  <div
                    className="ep-sched-head-meta"
                    data-testid="task-head-meta"
                  >
                    <span
                      className="ep-sched-head-customer"
                      data-testid="task-head-customer"
                    >
                      {poDetailLoading
                        ? "Memuat…"
                        : poDetail?.customer ?? "Belum tersedia"}
                    </span>
                    <span
                      className="ep-sched-head-po"
                      data-testid="task-head-po"
                    >
                      {selected.ref.po_no || poDetail?.poNo || "—"}
                    </span>
                    {selected.ref.sj_no ||
                    selected.ref.accurate_delivery_note_id ? (
                      <span data-testid="task-head-sj">
                        SJ{" "}
                        {selected.ref.sj_no ||
                          selected.ref.accurate_delivery_note_id}
                      </span>
                    ) : null}
                    <span
                      className={
                        poDetail?.urgent === true
                          ? "ep-task-status is-urgent"
                          : "ep-task-status is-belum"
                      }
                      data-testid="task-head-urgency"
                    >
                      {poDetailLoading
                        ? "…"
                        : urgencyLabel(poDetail?.urgent ?? null)}
                    </span>
                    <span
                      className="ep-muted"
                      data-testid="task-head-branch"
                      data-branch-missing-mapping={
                        pwaOps &&
                        !poDetailLoading &&
                        formatPwaBranchLabel({
                          branchId: poDetail?.branchId,
                          branchName: poDetail?.branchName,
                          branchNameVerified: poDetail?.branchNameVerified,
                        }).missingMapping
                          ? "true"
                          : undefined
                      }
                    >
                      {poDetailLoading
                        ? "…"
                        : pwaOps
                          ? formatPwaBranchLabel({
                              branchId: poDetail?.branchId,
                              branchName: poDetail?.branchName,
                              branchNameVerified: poDetail?.branchNameVerified,
                            }).label
                          : poDetail?.branchId
                            ? `Cabang ${poDetail.branchId}${
                                poDetail.branchName
                                  ? ` · ${poDetail.branchName}`
                                  : poDetail.branchNameVerified
                                    ? ""
                                    : " · belum diverifikasi"
                              }`
                            : "Cabang belum tersedia"}
                    </span>
                    <span
                      className={statusBadgeClass(selected.status)}
                      data-testid="task-head-task-status"
                    >
                      {statusLabel(selected.status)}
                    </span>
                  </div>
                ) : (
                  <div
                    className="ep-sched-head-meta"
                    data-testid="task-head-meta-driver"
                  >
                    <span
                      className="ep-sched-head-po"
                      data-testid="task-head-sj"
                    >
                      SJ{" "}
                      {selected.ref.sj_no ||
                        selected.ref.accurate_delivery_note_id ||
                        "—"}
                    </span>
                    {selected.ref.po_no ? (
                      <span className="ep-muted">PO {selected.ref.po_no}</span>
                    ) : null}
                    <span
                      className={statusBadgeClass(selected.status)}
                      data-testid="task-head-task-status"
                    >
                      {statusLabel(selected.status)}
                    </span>
                  </div>
                )}
              </div>
              <div className="ep-modal-head-actions">
                {selectedIsDesigner ? (
                  <button
                    type="button"
                    className="ep-btn sm"
                    disabled={headerReloading || poDetailLoading}
                    data-testid="designer-modal-reload"
                    title="Muat ulang data desain dan PO"
                    onClick={() => void onDesignerHeaderReload()}
                  >
                    {headerReloading || poDetailLoading
                      ? "Memuat…"
                      : "Muat ulang"}
                  </button>
                ) : null}
                {selectedIsScheduling ? (
                  <button
                    type="button"
                    className="ep-btn sm ep-btn-design"
                    data-testid="sched-view-design"
                    title="Lihat desain PO aktif"
                    onClick={() => schedDesignOpenRef.current?.()}
                  >
                    Lihat Design
                  </button>
                ) : null}
                <button
                  type="button"
                  className="ep-btn sm"
                  onClick={() => setSelected(null)}
                >
                  Tutup
                </button>
              </div>
            </header>
            <div className="ep-modal-body">
              {selectedIsDesigner ? (
                <div
                  className="ep-designer-summary"
                  data-testid="designer-summary-grid"
                >
                  <div className="ep-designer-summary-col">
                    <p className="ep-designer-summary-title">
                      Informasi Pesanan
                    </p>
                    <dl className="ep-kv ep-kv-compact">
                      <dt>Customer</dt>
                      <dd data-testid="designer-customer">
                        {poDetailLoading
                          ? "Memuat…"
                          : poDetailError
                            ? "—"
                            : poDetail?.customer ?? "Belum tersedia"}
                      </dd>
                      <dt>No. PO</dt>
                      <dd>{selected.ref.po_no || poDetail?.poNo || "—"}</dd>
                      <dt>Jenis Pekerjaan</dt>
                      <dd>{taskDisplayLabel(selected)}</dd>
                      <dt>Status Pesanan</dt>
                      <dd data-testid="designer-urgency">
                        {poDetailLoading
                          ? "Memuat…"
                          : urgencyLabel(poDetail?.urgent ?? null)}
                      </dd>
                    </dl>
                    {poDetailError ? (
                      <p className="ep-error ep-error-inline" role="alert">
                        Detail PO: {poDetailError}
                      </p>
                    ) : null}
                  </div>
                  <div className="ep-designer-summary-col">
                    <p className="ep-designer-summary-title">Item & Progres</p>
                    <dl className="ep-kv ep-kv-compact">
                      <dt>Jumlah item line</dt>
                      <dd data-testid="designer-item-count">
                        {poDetailLoading
                          ? "…"
                          : poDetail
                            ? poDetail.itemCount
                            : "—"}
                      </dd>
                      <dt>Item Cetak</dt>
                      <dd data-testid="designer-print-list">
                        {poDetailLoading ? (
                          "Memuat…"
                        ) : poDetail && poDetail.printLines.length > 0 ? (
                          <ul className="ep-designer-print-list">
                            {poDetail.printLines.map((line) => (
                              <li key={line.accurateLineId || line.itemCode}>
                                <span className="ep-designer-print-name">
                                  {line.itemName}
                                </span>
                                <span className="ep-designer-print-sep" aria-hidden>
                                  —
                                </span>
                                <span className="ep-designer-print-qty ep-num">
                                  {formatQty(line.qty)}{" "}
                                  {line.unit.toLowerCase()}
                                </span>
                              </li>
                            ))}
                          </ul>
                        ) : poDetail ? (
                          "Tidak ada item Cetak"
                        ) : (
                          "—"
                        )}
                      </dd>
                      <dt title="Indikator progres tugas desain — bukan Qty cup">
                        Qty Target tugas
                      </dt>
                      <dd className="ep-num">
                        {formatQty(selected.qty_target)}
                      </dd>
                      <dt>Qty Terkonfirmasi</dt>
                      <dd className="ep-num">
                        {formatQty(selected.qty_confirmed)}
                      </dd>
                      <dt>
                        {shortfallLabel(
                          selected.task_type,
                          selected.ref.process_kind ||
                            (selected.context?.process_kind as string | undefined),
                        )}
                      </dt>
                      <dd className="ep-num">
                        {formatQty(
                          selected.qty_target !== undefined
                            ? (remainingQty(selected) ?? 0)
                            : undefined,
                        )}
                      </dd>
                    </dl>
                  </div>
                </div>
              ) : selectedIsScheduling ? null : selectedIsDriver ? null : (
                <div
                  className="ep-task-progress-block"
                  data-testid="task-progress-table"
                >
                  <p className="ep-sectionlabel">Item & Progres</p>
                  <div className="ep-tblscroll ep-tblscroll-embed">
                    <table className="ep-table ep-sched-items-table">
                      <thead>
                        <tr>
                          <th>Nama Barang</th>
                          <th className="ep-num">
                            {selected.task_type === "screen" &&
                            selected.qty_target === undefined
                              ? "Qty Alokasi"
                              : "Qty Target"}
                          </th>
                          <th className="ep-num">Qty Terkonfirmasi</th>
                          <th className="ep-num">
                            {shortfallLabel(
                              selected.task_type,
                              selected.ref.process_kind ||
                                (selected.context?.process_kind as
                                  | string
                                  | undefined),
                            )}
                          </th>
                          <th>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <td className="ep-sched-td-text">
                            <span
                              className="ep-sched-item-name"
                              data-testid="task-popup-item-name"
                            >
                              {taskItemDisplayName(selected)}
                            </span>
                            <span className="ep-sched-item-code">
                              {taskItemSecondaryMeta(selected) ||
                                (selected.ref.allocation_id
                                  ? `alloc:${selected.ref.allocation_id}`
                                  : "—")}
                            </span>
                          </td>
                          <td className="ep-num">
                            {selected.qty_target !== undefined
                              ? formatQty(selected.qty_target)
                              : formatQty(screenAllocQty(selected) ?? undefined)}
                          </td>
                          <td className="ep-num">
                            {formatQty(selected.qty_confirmed)}
                          </td>
                          <td className="ep-num">
                            {selected.qty_target !== undefined
                              ? formatQty(remainingQty(selected) ?? 0)
                              : "—"}
                          </td>
                          <td>
                            <span className={statusBadgeClass(selected.status)}>
                              {statusLabel(selected.status)}
                            </span>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {selectedIsDesigner && selected.ref.accurate_po_id ? (
                <DesignWorkflowPanel
                  accuratePoId={selected.ref.accurate_po_id}
                  poNo={selected.ref.po_no}
                  compact
                  hideReloadButton
                  onRegisterReload={registerDesignReload}
                  onChanged={() => void reloadAfterAction(selected)}
                />
              ) : null}

              {isSchedulingTask(selected.task_type) ? (
                <TeamTaskSchedulingForm
                  task={selected}
                  canAllocate={
                    selected.task_type === "penjadwalan" &&
                    (canAct("penjadwalan", "confirm") ||
                      canAct("penjadwalan", "input"))
                  }
                  canRevise={
                    canAct(selected.task_type, "edit") ||
                    canAct(selected.task_type, "input") ||
                    canAct("penjadwalan", "edit")
                  }
                  busy={busy}
                  setBusy={setBusy}
                  onError={setActionError}
                  onNotice={setActionNotice}
                  onSuccess={showSuccess}
                  onChanged={() => reloadAfterAction(selected)}
                  onRegisterDesignOpen={registerSchedDesignOpen}
                />
              ) : null}

              {selectedIsSample ? (
                <>
                  <p className="ep-sectionlabel">Riwayat Sampel</p>
                  {sampleRows.length === 0 ? (
                    <p className="ep-empty">Belum ada sampel diunggah.</p>
                  ) : (
                    <table className="ep-table">
                      <thead>
                        <tr>
                          <th>Versi</th>
                          <th>File</th>
                          <th>Bukti</th>
                          <th>Status</th>
                          <th>Operator</th>
                          <th>Alasan Tolak</th>
                          {selected.task_type === SAMPLE_APPROVAL_TASK ? (
                            <th>Keputusan</th>
                          ) : null}
                        </tr>
                      </thead>
                      <tbody>
                        {sampleRows.map((s) => (
                          <tr key={s.sample_id}>
                            <td>{s.version}</td>
                            <td>{s.filename}</td>
                            <td>
                              {s.evidence_id && selected.ref.allocation_id ? (
                                <button
                                  type="button"
                                  className="ep-btn sm"
                                  data-testid="sample-preview-btn"
                                  onClick={() =>
                                    setEvidencePreview({
                                      title: `Sampel v${s.version}`,
                                      url: sampleEvidenceContentUrl(
                                        selected.ref.allocation_id!,
                                        s.evidence_id!,
                                      ),
                                      filename: s.filename,
                                    })
                                  }
                                >
                                  Preview
                                </button>
                              ) : (
                                "—"
                              )}
                            </td>
                            <td>
                              <span className={sampleStatusClass(s.status)}>
                                {sampleStatusLabel(s.status)}
                              </span>
                            </td>
                            <td>{s.submitted_by}</td>
                            <td>{s.reject_reason || "—"}</td>
                            {selected.task_type === SAMPLE_APPROVAL_TASK ? (
                              <td>
                                {s.status === "menunggu_approval" &&
                                canAct(SAMPLE_APPROVAL_TASK, "approve") ? (
                                  <>
                                    <button
                                      type="button"
                                      className="ep-btn primary sm"
                                      disabled={busy}
                                      onClick={() =>
                                        void onDecideSample(s.version, true)
                                      }
                                    >
                                      Setujui
                                    </button>{" "}
                                    <button
                                      type="button"
                                      className="ep-btn sm"
                                      disabled={busy}
                                      onClick={() =>
                                        void onDecideSample(s.version, false)
                                      }
                                    >
                                      Tolak
                                    </button>
                                  </>
                                ) : (
                                  "—"
                                )}
                              </td>
                            ) : null}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  {selected.task_type === SAMPLE_APPROVAL_TASK ? (
                    <div className="ep-filterbar">
                      <label htmlFor="reject-reason">Alasan tolak</label>
                      <input
                        id="reject-reason"
                        value={rejectReason}
                        onChange={(e) => setRejectReason(e.target.value)}
                      />
                    </div>
                  ) : canAct(SAMPLE_UPLOAD_TASK, "input") ||
                    canAct(SAMPLE_UPLOAD_TASK, "confirm") ? (
                    <div className="ep-filterbar">
                      <input
                        ref={fileRef}
                        type="file"
                        aria-label="File sampel"
                        accept="image/png,image/jpeg,image/webp,application/pdf"
                      />
                      <button
                        type="button"
                        className="ep-btn"
                        disabled={busy}
                        data-testid="sample-upload-draft-btn"
                        onClick={() => void onUploadSampleDraft()}
                      >
                        {busy ? "Mengunggah…" : "Unggah Draft"}
                      </button>
                      <button
                        type="button"
                        className="ep-btn primary"
                        disabled={busy || !latestDraftSample(sampleRows)}
                        data-testid="sample-confirm-btn"
                        onClick={() => void onConfirmSampleSubmission()}
                      >
                        {busy ? "Mengirim…" : "Konfirmasi"}
                      </button>
                    </div>
                  ) : null}
                </>
              ) : null}

              <div
                className={`ep-task-history-block${
                  selectedIsDesigner || selectedIsScheduling
                    ? " is-compact"
                    : ""
                }`}
                data-testid="task-history-block"
              >
                <p className="ep-sectionlabel">Riwayat Konfirmasi</p>
                {historyError ? (
                  <p className="ep-error" role="alert">
                    {historyError}{" "}
                    <button
                      type="button"
                      className="ep-btn sm"
                      onClick={() => void openTask(selected)}
                    >
                      Coba Lagi
                    </button>
                  </p>
                ) : history.length === 0 ? (
                  <p className="ep-empty ep-empty-inline">Belum ada konfirmasi.</p>
                ) : (
                  <ul className="ep-history">
                    {history.map((h) => (
                      <li key={h.idempotency_key}>
                        {formatQty(h.qty)} · {h.actor_email} ·{" "}
                        {formatDateTimeDisplay(h.created_at)}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {isLoadingSjTask(selected.task_type) ? (
                <div className="ep-loading-sj" data-testid="loading-sj-block">
                  <p className="ep-sectionlabel">Loading Surat Jalan</p>
                  <p>
                    SJ:{" "}
                    <strong>
                      {selected.ref.sj_no ||
                        selected.ref.accurate_delivery_note_id ||
                        "—"}
                    </strong>
                  </p>
                  {loadingValidationError ? (
                    <p className="ep-error">{loadingValidationError}</p>
                  ) : null}
                  {loadingValidation ? (
                    <div className="ep-tblscroll ep-tblscroll-embed">
                      <table className="ep-table">
                        <thead>
                          <tr>
                            <th>Nama Barang</th>
                            <th className="ep-num">Qty SJ</th>
                            <th className="ep-num">Qty Packing tersedia</th>
                            <th>Status</th>
                          </tr>
                        </thead>
                        <tbody>
                          {loadingValidation.items.map((it) => {
                            const name =
                              String(it.item_name || "").trim() ||
                              "Nama barang belum tersedia";
                            const meta = [
                              it.item_code
                                ? `Kode ${it.item_code}`
                                : null,
                              it.accurate_line_id
                                ? `Line ${it.accurate_line_id}`
                                : null,
                            ]
                              .filter(Boolean)
                              .join(" · ");
                            return (
                              <tr
                                key={String(it.accurate_line_id)}
                                className={
                                  it.matched ? undefined : "ep-row-danger"
                                }
                              >
                                <td className="ep-sched-td-text">
                                  <span className="ep-sched-item-name">
                                    {name}
                                  </span>
                                  {meta ? (
                                    <span className="ep-sched-item-code">
                                      {meta}
                                    </span>
                                  ) : null}
                                </td>
                                <td className="ep-num">
                                  {formatQty(Number(it.qty))}
                                </td>
                                <td className="ep-num">
                                  {formatQty(Number(it.qty_available))}
                                </td>
                                <td>
                                  {it.matched
                                    ? "Cocok"
                                    : it.reason || "Tidak cocok"}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  ) : null}
                  <p className="ep-notice">
                    Pilih Sesuai atau Tidak Sesuai — tanpa menginput ulang Qty SJ.
                    Backend mengulang validasi authoritative.
                  </p>
                  {(canAct("loading-sj", "confirm") ||
                    canAct(DRIVER_TASK, "confirm")) &&
                  selected.status !== "selesai" ? (
                    <div className="ep-btnrow">
                      <button
                        type="button"
                        className="ep-btn primary"
                        disabled={busy}
                        onClick={() => void onLoadingMatch()}
                      >
                        {busy ? "Menyimpan…" : "Sesuai"}
                      </button>
                      <button
                        type="button"
                        className="ep-btn"
                        disabled={busy}
                        onClick={() => void onLoadingMismatch()}
                      >
                        Tidak Sesuai
                      </button>
                    </div>
                  ) : null}
                </div>
              ) : null}

              {isCoordinationTask(selected.task_type) ? (
                <div className="ep-coordination" data-testid="coordination-block">
                  <p className="ep-sectionlabel">
                    {taskDisplayLabel(selected)}
                  </p>
                  {selected.ref.sj_no ||
                  selected.ref.accurate_delivery_note_id ? (
                    <p>
                      SJ:{" "}
                      <strong>
                        {selected.ref.sj_no ||
                          selected.ref.accurate_delivery_note_id}
                      </strong>
                    </p>
                  ) : null}
                  <p className="ep-coordination-message">
                    {String(
                      selected.context?.message ||
                        (selected.task_type === "pengiriman-gagal-koordinasi"
                          ? "Mohon koordinasi administrasi terkait pengiriman gagal."
                          : "Mohon konfirmasi ke administrasi karena qty loading dan SJ berbeda"),
                    )}
                  </p>
                  {selected.context?.failure_reason ? (
                    <p>
                      Alasan gagal:{" "}
                      <strong>{String(selected.context.failure_reason)}</strong>
                    </p>
                  ) : null}
                  {Array.isArray(selected.context?.mismatched_items) ? (
                    <table className="ep-table">
                      <thead>
                        <tr>
                          <th>Nama Barang</th>
                          <th>Qty SJ</th>
                          <th>Qty Tersedia</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(
                          selected.context.mismatched_items as {
                            accurate_line_id: string;
                            item_code?: string;
                            item_name?: string;
                            qty_sj: number;
                            qty_available: number;
                          }[]
                        ).map((m) => {
                          const name =
                            String(m.item_name || "").trim() ||
                            "Nama barang belum tersedia";
                          const meta = [
                            m.item_code ? `Kode ${m.item_code}` : null,
                            m.accurate_line_id
                              ? `Line ${m.accurate_line_id}`
                              : null,
                          ]
                            .filter(Boolean)
                            .join(" · ");
                          return (
                            <tr
                              key={m.accurate_line_id}
                              className="ep-row-danger"
                            >
                              <td className="ep-sched-td-text">
                                <span className="ep-sched-item-name">
                                  {name}
                                </span>
                                {meta ? (
                                  <span className="ep-sched-item-code">
                                    {meta}
                                  </span>
                                ) : null}
                              </td>
                              <td className="ep-num">
                                {formatQty(m.qty_sj)}
                              </td>
                              <td className="ep-num">
                                {formatQty(m.qty_available)}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  ) : null}
                  <p className="ep-notice">
                    Konfirmasi hanya menandai koordinasi selesai — tidak mengubah
                    SJ atau Qty pengiriman.
                  </p>
                  {canAct(selected.task_type, "confirm") ? (
                    <button
                      type="button"
                      className="ep-btn primary"
                      disabled={busy || selected.status === "selesai"}
                      onClick={() => void onConfirmCoordination()}
                    >
                      {busy ? "Menyimpan…" : "Konfirmasi"}
                    </button>
                  ) : null}
                </div>
              ) : null}

              {selected.task_type === DRIVER_TASK ? (
                <div data-testid="driver-block">
                  <dl className="ep-kv">
                    <dt>Driver (akun login)</dt>
                    <dd>{user?.email || "—"}</dd>
                    <dt>No. SJ</dt>
                    <dd>
                      {selected.ref.sj_no ||
                        selected.ref.accurate_delivery_note_id ||
                        "—"}
                    </dd>
                  </dl>
                  {deliveryDetailError ? (
                    <p className="ep-error" role="alert">
                      {deliveryDetailError}
                    </p>
                  ) : null}
                  {deliveryDetail ? (
                    <>
                      <p className="ep-sectionlabel">Riwayat & Bukti (baca)</p>
                      <ul className="ep-history">
                        {deliveryDetail.events.map((ev) => (
                          <li key={`${ev.attempt}-${ev.created_at}`}>
                            Attempt {ev.attempt}: {ev.status}
                            {ev.recipient_name
                              ? ` · ${ev.recipient_name}`
                              : ""}
                            {ev.failure_reason
                              ? ` · gagal: ${ev.failure_reason}`
                              : ""}
                            {ev.created_at
                              ? ` · ${formatDateTimeDisplay(ev.created_at)}`
                              : ""}
                            {" · "}
                            {ev.photo_evidence_ids.length} foto
                            {ev.photo_evidence_ids.length > 0 &&
                            selected.ref.accurate_delivery_note_id ? (
                              <span className="ep-evidence-inline-actions">
                                {ev.photo_evidence_ids.map((eid, idx) => (
                                  <button
                                    key={eid}
                                    type="button"
                                    className="ep-btn sm"
                                    data-testid="delivery-photo-preview-btn"
                                    onClick={() =>
                                      setEvidencePreview({
                                        title: `Foto pengiriman #${idx + 1}`,
                                        url: deliveryEvidenceContentUrl(
                                          selected.ref
                                            .accurate_delivery_note_id!,
                                          eid,
                                        ),
                                        filename: `photo-${idx + 1}.jpg`,
                                      })
                                    }
                                  >
                                    Foto {idx + 1}
                                  </button>
                                ))}
                              </span>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                      <p className="ep-notice">
                        Soft-delete bukti pengiriman hanya di menu Jadwal
                        Pengiriman.
                      </p>
                    </>
                  ) : null}

                  {canAct(DRIVER_TASK, "confirm") ? (
                    <>
                      <p className="ep-sectionlabel">Pengiriman Berhasil</p>
                      <div className="ep-filterbar">
                        <label htmlFor="recipient-name">Nama penerima</label>
                        <input
                          id="recipient-name"
                          value={recipientName}
                          onChange={(e) => setRecipientName(e.target.value)}
                        />
                        <input
                          ref={photosRef}
                          type="file"
                          multiple
                          aria-label="Foto bukti pengiriman"
                          accept="image/png,image/jpeg,image/webp"
                        />
                      </div>
                      <canvas
                        ref={signatureRef}
                        width={360}
                        height={140}
                        className="ep-signature"
                        data-testid="delivery-signature"
                        onPointerDown={startSignature}
                        onPointerMove={moveSignature}
                        onPointerUp={endSignature}
                        onPointerLeave={endSignature}
                      />
                      <div className="ep-filterbar">
                        <button
                          type="button"
                          className="ep-btn primary"
                          disabled={busy}
                          onClick={() => void onDeliverySuccess()}
                        >
                          {busy
                            ? "Menyimpan…"
                            : "Konfirmasi Pengiriman Berhasil"}
                        </button>
                      </div>

                      <p className="ep-sectionlabel">Pengiriman Gagal</p>
                      <div className="ep-filterbar">
                        <label htmlFor="failure-reason">Alasan</label>
                        <input
                          id="failure-reason"
                          value={failureReason}
                          onChange={(e) => setFailureReason(e.target.value)}
                        />
                        <button
                          type="button"
                          className="ep-btn"
                          disabled={busy}
                          onClick={() => void onDeliveryFailed()}
                        >
                          Pengiriman Gagal
                        </button>
                      </div>
                    </>
                  ) : (
                    <p className="ep-notice">Mode lihat — tanpa aksi mutasi.</p>
                  )}
                </div>
              ) : null}

              {canShowQtyConfirmForm(selected) &&
              selected.task_type !== DRIVER_TASK &&
              selected.task_type !== DESIGN_TASK &&
              !isLoadingSjTask(selected.task_type) &&
              !isCoordinationTask(selected.task_type) &&
              !selectedIsSample &&
              !isSchedulingTask(selected.task_type) &&
              (canAct(selected.task_type, "confirm") ||
                canAct(selected.task_type, "input")) ? (
                <div
                  className="ep-filterbar"
                  data-testid="qty-confirm-form"
                >
                  {selected.task_type === "screen" &&
                  selected.qty_target === undefined ? (
                    <p className="ep-notice" data-testid="screen-qty-hint">
                      Screen: input Qty hasil pekerjaan (tidak dibatasi Qty
                      jadwal). Qty harus &gt; 0.
                      {screenAllocQty(selected) !== null
                        ? ` Qty Alokasi: ${formatQty(screenAllocQty(selected) ?? undefined)}.`
                        : ""}
                    </p>
                  ) : null}
                  <label htmlFor="qty-confirm">Qty</label>
                  <input
                    id="qty-confirm"
                    data-testid="qty-confirm-input"
                    type="number"
                    min={1}
                    value={qtyInput}
                    onChange={(e) => setQtyInput(e.target.value)}
                  />
                  <button
                    type="button"
                    className="ep-btn primary"
                    data-testid="qty-confirm-btn"
                    disabled={busy}
                    onClick={() => void onConfirm()}
                  >
                    {busy ? "Menyimpan…" : "Konfirmasi"}
                  </button>
                </div>
              ) : null}

              {actionError ? (
                <p className="ep-error" role="alert">
                  {actionError}
                </p>
              ) : null}
              {actionNotice ? <p className="ep-notice">{actionNotice}</p> : null}
            </div>
            <footer className="ep-modal-foot">
              <span className="ep-muted">
                {busy
                  ? "Memproses…"
                  : selectedIsDesigner
                    ? "Desain · MongoDB V4"
                    : selectedIsScheduling
                      ? "Penjadwalan · alokasi operator"
                      : "Backend tetap otorisasi final."}
              </span>
              <div className="ep-modal-foot-actions">
                <button
                  type="button"
                  className="ep-btn sm"
                  onClick={() => setSelected(null)}
                >
                  Tutup
                </button>
              </div>
            </footer>
            <ConfirmationSuccessOverlay
              open={successOpen}
              global
              tone={successTone}
              title={successTitle}
              message={successMsg}
              autoCloseMs={successTone === "success" ? 1800 : 2600}
              onClose={() => setSuccessOpen(false)}
            />
          </div>
        </div>
      ) : null}

      {evidencePreview ? (
        <EvidenceContentPreview
          title={evidencePreview.title}
          contentUrl={evidencePreview.url}
          filename={evidencePreview.filename}
          onClose={() => setEvidencePreview(null)}
        />
      ) : null}
    </div>
  );
}

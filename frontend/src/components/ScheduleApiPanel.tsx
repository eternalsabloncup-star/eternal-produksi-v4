import { useCallback, useEffect, useState } from "react";
import {
  createAllocations,
  fetchPoSchedule,
  reviseAllocationQty,
  type LineSchedule,
} from "../api/scheduling";
import { newIdempotencyKey } from "../api/operational";
import { getApiPoSource } from "../demo/ApiPoSource";

function qty(n: number): string {
  return n.toLocaleString("id-ID");
}

function gateBadge(state: string): string {
  if (state === "open") return "Terbuka";
  if (state === "review") return "Perlu Review";
  return "Tertutup";
}

/** Jadwal Produksi — alokasi operator nyata dari MongoDB V4. */
export function ScheduleApiPanel() {
  const [lines, setLines] = useState<LineSchedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selected, setSelected] = useState<LineSchedule | null>(null);
  const [operator, setOperator] = useState("");
  const [allocQty, setAllocQty] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await getApiPoSource().list({ page: 1, pageSize: 25 });
      const collected: LineSchedule[] = [];
      for (const row of page.items) {
        if (!row.accurateId) continue;
        const res = await fetchPoSchedule(row.accurateId);
        collected.push(...res.lines);
      }
      setLines(collected);
    } catch (err) {
      setLines([]);
      setError(err instanceof Error ? err.message : "Gagal memuat jadwal.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function refreshSelected(line: LineSchedule) {
    const res = await fetchPoSchedule(line.accurate_po_id);
    const fresh =
      res.lines.find((l) => l.accurate_line_id === line.accurate_line_id) || null;
    setSelected(fresh);
    setLines((prev) =>
      prev.map((l) =>
        l.accurate_line_id === line.accurate_line_id && fresh ? fresh : l,
      ),
    );
  }

  async function onAllocate() {
    if (!selected) return;
    const value = Number(allocQty);
    if (!operator.trim() || !Number.isFinite(value) || value <= 0) {
      setError("Operator dan Qty alokasi wajib diisi.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await createAllocations(
        selected.accurate_po_id,
        selected.accurate_line_id,
        [{ operator_email: operator.trim(), qty: value }],
        newIdempotencyKey(`alloc:${selected.accurate_line_id}`),
      );
      setNotice(
        res.duplicate
          ? "Perintah sama sudah tercatat — jadwal tidak digandakan."
          : `Alokasi ${res.allocations[0].allocation_id} tersimpan.`,
      );
      setOperator("");
      setAllocQty("");
      await refreshSelected(selected);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Alokasi gagal.");
    } finally {
      setBusy(false);
    }
  }

  async function onRevise(allocationId: string, current: number) {
    const input = window.prompt("Qty alokasi baru", String(current));
    if (input === null) return;
    const value = Number(input);
    if (!Number.isFinite(value) || value < 0) {
      setError("Qty revisi tidak valid.");
      return;
    }
    const reason = window.prompt("Alasan revisi") || "";
    if (!reason.trim()) {
      setError("Alasan revisi wajib diisi.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await reviseAllocationQty(allocationId, value, reason.trim());
      setNotice(`Alokasi ${allocationId} direvisi menjadi ${qty(value)}.`);
      if (selected) await refreshSelected(selected);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Revisi gagal.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid="schedule-api-panel">
      <div className="ep-filterbar">
        <strong>Jadwal Produksi · MongoDB V4</strong>
        <button type="button" className="ep-btn" onClick={() => void load()}>
          Muat ulang
        </button>
      </div>

      {error ? <p className="ep-error">{error}</p> : null}
      {notice ? <p className="ep-notice">{notice}</p> : null}

      <div className="ep-tblscroll">
        <table className="ep-table">
          <thead>
            <tr>
              <th>No. PO</th>
              <th>Item Line</th>
              <th>Nama Barang</th>
              <th>Qty Item</th>
              <th>Qty Teralokasi</th>
              <th>Belum Dialokasikan</th>
              <th>Qty Cetak</th>
              <th>Gate Design</th>
              <th>Gate Payment</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={10}>Memuat jadwal dari MongoDB V4…</td>
              </tr>
            ) : lines.length === 0 ? (
              <tr>
                <td colSpan={10} className="ep-empty">
                  Belum ada item Cetak Accurate di MongoDB V4.
                </td>
              </tr>
            ) : (
              lines.map((l) => (
                <tr key={`${l.accurate_po_id}-${l.accurate_line_id}`}>
                  <td>{l.po_no}</td>
                  <td>{l.accurate_line_id}</td>
                  <td>{l.item_name}</td>
                  <td>{qty(l.qty_target)}</td>
                  <td>{qty(l.qty_allocated)}</td>
                  <td>{qty(l.qty_unallocated)}</td>
                  <td>{qty(l.qty_printed)}</td>
                  <td>{gateBadge(l.gates.design.state)}</td>
                  <td>{gateBadge(l.gates.payment.state)}</td>
                  <td>
                    <button
                      type="button"
                      className="ep-btn"
                      disabled={!l.scheduling_unlocked}
                      title={
                        l.scheduling_unlocked
                          ? "Atur alokasi operator"
                          : "Gate Design/Payment belum terpenuhi"
                      }
                      onClick={() => {
                        setSelected(l);
                        setNotice(null);
                        setError(null);
                      }}
                    >
                      Penjadwalan
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {selected ? (
        <div
          className="ep-modalshade ep-modalshade-center"
          onClick={(e) => {
            if (e.target === e.currentTarget) setSelected(null);
          }}
        >
          <div className="ep-modal" role="dialog" aria-modal="true">
            <header className="ep-modalhead">
              <strong>
                Penjadwalan · {selected.po_no} · line {selected.accurate_line_id}
              </strong>
              <button type="button" className="ep-btn" onClick={() => setSelected(null)}>
                Tutup
              </button>
            </header>
            <div className="ep-modalbody">
              <dl className="ep-kv">
                <dt>Qty Item</dt>
                <dd>{qty(selected.qty_target)}</dd>
                <dt>Belum Dialokasikan</dt>
                <dd>{qty(selected.qty_unallocated)}</dd>
                <dt>Qty Cetak Terkonfirmasi</dt>
                <dd>{qty(selected.qty_printed)}</dd>
              </dl>

              <p className="ep-sectionlabel">Alokasi Operator</p>
              {selected.allocations.length === 0 ? (
                <p className="ep-empty">Belum ada alokasi.</p>
              ) : (
                <table className="ep-table">
                  <thead>
                    <tr>
                      <th>Allocation ID</th>
                      <th>Operator</th>
                      <th>Qty</th>
                      <th>Qty Cetak</th>
                      <th>Status</th>
                      <th>Aksi</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selected.allocations.map((a) => (
                      <tr key={a.allocation_id}>
                        <td>{a.allocation_id}</td>
                        <td>{a.operator_email}</td>
                        <td>{qty(a.qty)}</td>
                        <td>{qty(a.qty_printed || 0)}</td>
                        <td>{a.status}</td>
                        <td>
                          <button
                            type="button"
                            className="ep-btn"
                            disabled={busy || (a.qty_printed || 0) > 0}
                            title={
                              (a.qty_printed || 0) > 0
                                ? "Sudah ada Qty cetak terkonfirmasi"
                                : "Ubah Qty alokasi"
                            }
                            onClick={() => void onRevise(a.allocation_id, a.qty)}
                          >
                            Ubah Qty
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              <p className="ep-sectionlabel">Tambah Alokasi</p>
              <div className="ep-filterbar">
                <input
                  aria-label="Operator"
                  placeholder="email operator"
                  value={operator}
                  onChange={(e) => setOperator(e.target.value)}
                />
                <input
                  aria-label="Qty alokasi"
                  type="number"
                  min={1}
                  value={allocQty}
                  onChange={(e) => setAllocQty(e.target.value)}
                />
                <button
                  type="button"
                  className="ep-btn primary"
                  disabled={busy}
                  onClick={() => void onAllocate()}
                >
                  {busy ? "Menyimpan…" : "Konfirmasi Jadwal"}
                </button>
              </div>
              {error ? <p className="ep-error">{error}</p> : null}
              {notice ? <p className="ep-notice">{notice}</p> : null}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

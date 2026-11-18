import { useCallback, useEffect, useMemo, useState } from "react";
import {
  confirmLoading,
  fetchDeliverySchedule,
  fetchLoadingValidation,
  raiseDiscrepancy,
  type DeliverySchedule,
  type DeliveryScheduleRow,
  type LoadingValidation,
} from "../api/delivery";
import { newIdempotencyKey } from "../api/operational";
import { KpiSummary, type KpiItem } from "./KpiSummary";

function qty(n: number): string {
  return n.toLocaleString("id-ID");
}

function statusLabel(status: string): string {
  if (status === "berhasil") return "Berhasil";
  if (status === "gagal") return "Gagal";
  return "Menunggu Pengiriman";
}

/** Jadwal Pengiriman — satu baris per Surat Jalan Accurate nyata. */
export function DeliveryApiPanel() {
  const [schedule, setSchedule] = useState<DeliverySchedule | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [selected, setSelected] = useState<DeliveryScheduleRow | null>(null);
  const [validation, setValidation] = useState<LoadingValidation | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSchedule(await fetchDeliverySchedule({ from: from || undefined, to: to || undefined }));
    } catch (err) {
      setSchedule(null);
      setError(err instanceof Error ? err.message : "Gagal memuat jadwal pengiriman.");
    } finally {
      setLoading(false);
    }
  }, [from, to]);

  useEffect(() => {
    void load();
  }, [load]);

  const kpis = useMemo((): KpiItem[] => {
    const summary = schedule?.summary;
    const units = Object.entries(summary?.qty_by_unit || {});
    return [
      {
        label: "Jumlah SJ",
        value: summary?.sj_count ?? 0,
        foot: "Surat Jalan Accurate",
      },
      {
        label: "Total Qty",
        value: units.map(([unit, v]) => `${qty(v)} ${unit}`).join(" + ") || "—",
        foot: "per satuan, tanpa konversi",
      },
      {
        label: "Tujuan Unik",
        value: summary?.destination_count ?? 0,
        foot: "alamat berbeda",
      },
      {
        label: "Sudah Loading",
        value: (schedule?.items || []).filter((r) => r.loaded).length,
        foot: "SJ siap dikirim",
      },
    ];
  }, [schedule]);

  async function openLoading(row: DeliveryScheduleRow, keepNotice = false) {
    setSelected(row);
    setValidation(null);
    setError(null);
    if (!keepNotice) setNotice(null);
    try {
      setValidation(await fetchLoadingValidation(row.accurate_sj_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Validasi Loading gagal.");
    }
  }

  async function onConfirmLoading() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const res = await confirmLoading(
        selected.accurate_sj_id,
        newIdempotencyKey(`loading:${selected.accurate_sj_id}`),
      );
      const message = res.duplicate
        ? "Loading SJ ini sudah dikonfirmasi sebelumnya."
        : "Loading dikonfirmasi; tugas Driver dibuat.";
      await load();
      await openLoading(selected, true);
      setNotice(message);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Konfirmasi Loading gagal.");
    } finally {
      setBusy(false);
    }
  }

  async function onDiscrepancy() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await raiseDiscrepancy(selected.accurate_sj_id);
      setNotice(
        "Tugas Kepala Produksi “Qty Loading dan SJ Berbeda” dibuat.",
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal membuat tugas koordinasi.");
    } finally {
      setBusy(false);
    }
  }

  const rows = schedule?.items || [];

  return (
    <div data-testid="delivery-api-panel">
      <KpiSummary items={kpis} />

      <div className="ep-filterbar">
        <label htmlFor="ship-from">Tanggal kirim</label>
        <input
          id="ship-from"
          type="date"
          value={from}
          onChange={(e) => setFrom(e.target.value)}
        />
        <label htmlFor="ship-to">s/d</label>
        <input id="ship-to" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        <button type="button" className="ep-btn" onClick={() => void load()}>
          Terapkan
        </button>
        <button
          type="button"
          className="ep-btn"
          onClick={() => {
            setFrom("");
            setTo("");
          }}
        >
          Reset filter
        </button>
      </div>

      {error ? <p className="ep-error">{error}</p> : null}
      {notice ? <p className="ep-notice">{notice}</p> : null}

      <div className="ep-tblscroll">
        <table className="ep-table">
          <thead>
            <tr>
              <th>No. SJ</th>
              <th>No. PO</th>
              <th>Tanggal Kirim</th>
              <th>Tujuan</th>
              <th>Ekspedisi</th>
              <th>Item</th>
              <th>Loading</th>
              <th>Status Pengiriman</th>
              <th>Aksi</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={9}>Memuat Surat Jalan dari MongoDB V4…</td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={9} className="ep-empty">
                  Belum ada Surat Jalan Accurate di MongoDB V4.
                </td>
              </tr>
            ) : (
              rows.flatMap((row) => {
                const main = (
                  <tr
                    key={row.accurate_sj_id}
                    onClick={() =>
                      setExpanded(expanded === row.accurate_sj_id ? null : row.accurate_sj_id)
                    }
                  >
                    <td>{row.sj_no}</td>
                    <td>{row.po_no}</td>
                    <td>{row.ship_date}</td>
                    <td>{row.destination}</td>
                    <td>{row.shipment || "—"}</td>
                    <td>{row.items.length}</td>
                    <td>{row.loaded ? "Sudah" : "Belum"}</td>
                    <td>{statusLabel(row.delivery_status)}</td>
                    <td>
                      <button
                        type="button"
                        className="ep-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          void openLoading(row);
                        }}
                      >
                        Loading
                      </button>
                    </td>
                  </tr>
                );
                if (expanded !== row.accurate_sj_id) return [main];
                return [
                  main,
                  <tr key={`${row.accurate_sj_id}-items`}>
                    <td colSpan={9}>
                      <table className="ep-table">
                        <thead>
                          <tr>
                            <th>Baris SJ</th>
                            <th>Item Line PO</th>
                            <th>Kode</th>
                            <th>Nama Barang</th>
                            <th>Qty</th>
                            <th>Satuan</th>
                          </tr>
                        </thead>
                        <tbody>
                          {row.items.map((item) => (
                            <tr key={item.sj_line_id}>
                              <td>{item.sj_line_id}</td>
                              <td>{item.accurate_line_id}</td>
                              <td>{item.item_code}</td>
                              <td>{item.item_name}</td>
                              <td>{qty(item.qty)}</td>
                              <td>{item.unit}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </td>
                  </tr>,
                ];
              })
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
              <strong>Rincian Pengiriman · {selected.sj_no}</strong>
              <button type="button" className="ep-btn" onClick={() => setSelected(null)}>
                Tutup
              </button>
            </header>
            <div className="ep-modalbody">
              <dl className="ep-kv">
                <dt>No. PO</dt>
                <dd>{selected.po_no}</dd>
                <dt>Tujuan</dt>
                <dd>{selected.destination}</dd>
                <dt>Tanggal Kirim</dt>
                <dd>{selected.ship_date}</dd>
                <dt>Status Loading</dt>
                <dd>{selected.loaded ? "Sudah dikonfirmasi" : "Belum"}</dd>
              </dl>

              <p className="ep-sectionlabel">Validasi Qty Loading</p>
              {validation === null ? (
                <p className="ep-empty">Memuat validasi…</p>
              ) : (
                <table className="ep-table">
                  <thead>
                    <tr>
                      <th>Item Line</th>
                      <th>Proses</th>
                      <th>Qty SJ</th>
                      <th>Qty Tersedia</th>
                      <th>Cocok</th>
                      <th>Keterangan</th>
                    </tr>
                  </thead>
                  <tbody>
                    {validation.items.map((item) => (
                      <tr
                        key={item.sj_line_id}
                        className={item.matched ? undefined : "ep-row-danger"}
                      >
                        <td>{item.accurate_line_id}</td>
                        <td>{item.process_kind || "—"}</td>
                        <td>{qty(item.qty)}</td>
                        <td>{qty(item.qty_available)}</td>
                        <td>{item.matched ? "Ya" : "Tidak"}</td>
                        <td>{item.reason || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              <div className="ep-filterbar">
                <button
                  type="button"
                  className="ep-btn primary"
                  disabled={busy || !validation?.all_matched || selected.loaded}
                  title={
                    selected.loaded
                      ? "SJ ini sudah di-Loading"
                      : validation?.all_matched
                        ? "Konfirmasi Loading untuk seluruh item SJ"
                        : "Qty item belum cocok dengan SJ"
                  }
                  onClick={() => void onConfirmLoading()}
                >
                  Konfirmasi Loading
                </button>
                {validation && !validation.all_matched ? (
                  <button
                    type="button"
                    className="ep-btn"
                    disabled={busy}
                    onClick={() => void onDiscrepancy()}
                  >
                    Perbedaan Qty
                  </button>
                ) : null}
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

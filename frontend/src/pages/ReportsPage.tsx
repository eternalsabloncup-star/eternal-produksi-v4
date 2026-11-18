import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchReport, REPORT_KINDS } from "../api/analytics";

function cell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  if (typeof value === "number") return value.toLocaleString("id-ID");
  if (typeof value === "boolean") return value ? "Ya" : "Tidak";
  return String(value);
}

/** Laporan operasional dari ledger dan event nyata (tanpa laba/HPP). */
export function ReportsPage() {
  const [kind, setKind] = useState(REPORT_KINDS[0].id);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [actor, setActor] = useState("");
  const [po, setPo] = useState("");
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchReport(kind, {
        from: from || undefined,
        to: to || undefined,
        actor: actor || undefined,
        po: po || undefined,
      });
      setRows(res.items);
    } catch (err) {
      setRows([]);
      setError(err instanceof Error ? err.message : "Gagal memuat laporan.");
    } finally {
      setLoading(false);
    }
  }, [kind, from, to, actor, po]);

  useEffect(() => {
    void load();
  }, [load]);

  const columns = useMemo(() => {
    const keys = new Set<string>();
    for (const row of rows.slice(0, 20)) {
      for (const key of Object.keys(row)) keys.add(key);
    }
    return Array.from(keys);
  }, [rows]);

  return (
    <div data-testid="reports-api">
      <div className="ep-filterbar">
        <label htmlFor="report-kind">Laporan</label>
        <select
          id="report-kind"
          value={kind}
          onChange={(e) => setKind(e.target.value)}
        >
          {REPORT_KINDS.map((r) => (
            <option key={r.id} value={r.id}>
              {r.label}
            </option>
          ))}
        </select>
        <label htmlFor="report-from">Periode</label>
        <input
          id="report-from"
          type="date"
          value={from}
          onChange={(e) => setFrom(e.target.value)}
        />
        <label htmlFor="report-to">s/d</label>
        <input
          id="report-to"
          type="date"
          value={to}
          onChange={(e) => setTo(e.target.value)}
        />
        <input
          aria-label="Petugas"
          placeholder="email petugas"
          value={actor}
          onChange={(e) => setActor(e.target.value)}
        />
        <input
          aria-label="Accurate PO ID"
          placeholder="Accurate PO ID"
          value={po}
          onChange={(e) => setPo(e.target.value)}
        />
        <button type="button" className="ep-btn" onClick={() => void load()}>
          Terapkan
        </button>
      </div>

      {error ? <p className="ep-error">{error}</p> : null}

      <div className="ep-tblscroll">
        <table className="ep-table">
          <thead>
            <tr>
              {columns.length === 0 ? <th>Data</th> : columns.map((c) => <th key={c}>{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={Math.max(columns.length, 1)}>Memuat laporan…</td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan={Math.max(columns.length, 1)} className="ep-empty">
                  Belum ada data untuk filter ini.
                </td>
              </tr>
            ) : (
              rows.map((row, index) => (
                <tr key={index}>
                  {columns.map((c) => (
                    <td key={c}>{cell(row[c])}</td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <p className="ep-sectionlabel">
        {rows.length} baris · sumber ledger MongoDB V4
      </p>
    </div>
  );
}

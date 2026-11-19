import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchReport, REPORT_KINDS } from "../api/analytics";
import { DataTable } from "../components/DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "../components/DataTable/Pagination";
import type { ColumnDef } from "../components/DataTable/types";

function cell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  if (typeof value === "number") return value.toLocaleString("id-ID");
  if (typeof value === "boolean") return value ? "Ya" : "Tidak";
  return String(value);
}

type ReportRow = Record<string, unknown> & { __rowKey: string };

/** Laporan operasional dari ledger dan event nyata (tanpa laba/HPP). */
export function ReportsPage() {
  const [kind, setKind] = useState(REPORT_KINDS[0].id);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [actor, setActor] = useState("");
  const [po, setPo] = useState("");
  const [rows, setRows] = useState<ReportRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);

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
      setRows(
        res.items.map((item, index) => ({
          ...item,
          __rowKey: String(
            (item as { id?: unknown }).id ??
              (item as { task_key?: unknown }).task_key ??
              (item as { accurate_po_id?: unknown }).accurate_po_id ??
              index,
          ),
        })),
      );
      setPage(1);
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

  const columnKeys = useMemo(() => {
    const keys = new Set<string>();
    for (const row of rows.slice(0, 40)) {
      for (const key of Object.keys(row)) {
        if (key !== "__rowKey") keys.add(key);
      }
    }
    return Array.from(keys);
  }, [rows]);

  const columns: ColumnDef<ReportRow>[] = useMemo(
    () =>
      columnKeys.map((key) => ({
        id: key,
        label: key,
        hideable: true,
        filterType: "text" as const,
        defaultWidth: 140,
        getFilterValue: (row) => cell(row[key]),
        getSortValue: (row) => {
          const v = row[key];
          if (typeof v === "number") return v;
          return cell(v);
        },
        render: (row) => cell(row[key]),
      })),
    [columnKeys],
  );

  const pageRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return rows.slice(start, start + pageSize);
  }, [rows, page, pageSize]);

  return (
    <div data-testid="reports-api">
      {error ? <p className="ep-error">{error}</p> : null}

      <DataTable
        tableId={`po-accurate:reports-${kind}`}
        columns={
          columns.length > 0
            ? columns
            : [
                {
                  id: "placeholder",
                  label: "Data",
                  hideable: false,
                  render: () => "—",
                },
              ]
        }
        rows={loading ? [] : pageRows}
        filterSourceRows={rows}
        rowKey={(r) => r.__rowKey}
        enableColumnManager
        inlineColumnTools
        page={page}
        pageSize={pageSize}
        total={loading ? 0 : rows.length}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        emptyMessage={
          loading
            ? "Memuat laporan…"
            : "Belum ada data untuk filter ini."
        }
        filterSlot={(tools) => (
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
          </div>
        )}
      />
      <p className="ep-sectionlabel">
        {rows.length} baris · sumber ledger MongoDB V4 · rumus tidak diubah
      </p>
    </div>
  );
}

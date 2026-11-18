import { useMemo, useState } from "react";
import type { TeamTaskRole } from "../app/nav";
import { formatDateDisplay } from "../lib/formatDate";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";
import {
  TEAM_TASK_STATUS_OPTIONS,
  listTeamTasks,
  type TeamTaskFilters,
  type TeamTaskRow,
} from "../demo/teamTasks";

type Props = {
  role: TeamTaskRole;
};

/** Tabel tugas per peran — shared DataTable, tanpa field bisnis spekulatif. */
export function TeamTaskTable({ role }: Props) {
  const [filters, setFilters] = useState<TeamTaskFilters>({});
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);

  const { items, total } = useMemo(
    () => listTeamTasks(role.id, filters),
    [role.id, filters],
  );

  const pageRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return items.slice(start, start + pageSize);
  }, [items, page, pageSize]);

  const columns: ColumnDef<TeamTaskRow>[] = useMemo(
    () => [
      {
        id: "id",
        label: "ID Tugas",
        hideable: false,
        filterType: "text",
        defaultWidth: 110,
        getFilterValue: (row) => row.id,
        render: (row) => <span className="key">{row.id}</span>,
      },
      {
        id: "title",
        label: "Judul",
        hideable: true,
        filterType: "text",
        defaultWidth: 180,
        getFilterValue: (row) => row.title,
        render: (row) => row.title,
      },
      {
        id: "poRef",
        label: "Ref. PO",
        hideable: true,
        filterType: "text",
        defaultWidth: 120,
        getFilterValue: (row) => row.poRef,
        render: (row) => row.poRef,
      },
      {
        id: "status",
        label: "Status",
        hideable: true,
        filterType: "text",
        defaultWidth: 110,
        getFilterValue: (row) => row.status,
        render: (row) => row.status,
      },
      {
        id: "due",
        label: "Target",
        hideable: true,
        filterType: "date",
        defaultWidth: 110,
        getFilterValue: (row) => row.due,
        getSortValue: (row) => row.due,
        render: (row) => formatDateDisplay(row.due),
      },
    ],
    [],
  );

  return (
    <DataTable
      tableId={`team-tasks:${role.id}`}
      columns={columns}
      rows={pageRows}
      filterSourceRows={items}
      rowKey={(row) => row.id}
      enableColumnManager
      inlineColumnTools
      page={page}
      pageSize={pageSize}
      total={total}
      onPageChange={setPage}
      onPageSizeChange={(size) => {
        setPageSize(size);
        setPage(1);
      }}
      filterSlot={(tools) => (
        <div className="ep-filterbar is-compact">
          <div className="ep-search">
            <span style={{ fontSize: 14, color: "#97a8bc" }} aria-hidden>
              ⌕
            </span>
            <input
              placeholder="Cari judul atau ref. PO…"
              value={filters.q || ""}
              onChange={(e) => {
                setFilters((f) => ({ ...f, q: e.target.value }));
                setPage(1);
              }}
              aria-label="Cari tugas"
            />
          </div>
          <select
            value={filters.status || ""}
            onChange={(e) => {
              setFilters((f) => ({
                ...f,
                status: e.target.value || undefined,
              }));
              setPage(1);
            }}
            aria-label="Filter status"
          >
            <option value="">Semua status</option>
            {TEAM_TASK_STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="ep-btn sm"
            onClick={() => {
              setFilters({});
              setPage(1);
            }}
          >
            Reset filter
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
      emptyMessage={
        <>
          Belum ada tugas untuk peran <strong>{role.label}</strong> (DEMO).
        </>
      }
    />
  );
}

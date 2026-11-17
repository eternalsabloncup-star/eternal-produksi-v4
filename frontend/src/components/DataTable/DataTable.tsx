import type { ReactNode } from "react";
import type { ColumnDef } from "./types";
import { Pagination } from "./Pagination";

type Props<T> = {
  columns: ColumnDef<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  hiddenColumns?: Set<string>;
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  emptyMessage?: ReactNode;
  toolbar?: ReactNode;
  filterSlot?: ReactNode;
};

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  hiddenColumns,
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  emptyMessage,
  toolbar,
  filterSlot,
}: Props<T>) {
  const visible = columns.filter((c) => !hiddenColumns?.has(c.id));

  return (
    <div className="ep-panel">
      {toolbar}
      {filterSlot}
      <div className="ep-tblscroll">
        <table className="ep-tbl">
          <thead>
            <tr>
              {visible.map((col) => (
                <th
                  key={col.id}
                  className={
                    col.sticky === "po" ? "sticky1" : col.sticky === "customer" ? "sticky2" : undefined
                  }
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={Math.max(visible.length, 1)}>
                  <div className="ep-empty">
                    {emptyMessage ?? (
                      <>
                        Tidak ada data sesuai filter.
                        <br />
                        <small>Coba ubah filter atau pilih cabang lain.</small>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={rowKey(row)}>
                  {visible.map((col) => (
                    <td
                      key={col.id}
                      className={
                        col.sticky === "po"
                          ? "sticky1"
                          : col.sticky === "customer"
                            ? "sticky2"
                            : undefined
                      }
                    >
                      {col.render(row)}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <Pagination
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
      />
    </div>
  );
}

import type { ReactNode } from "react";

export type ColumnFilterKind = "text" | "number" | "date";

export type ColumnDef<T> = {
  id: string;
  label: string;
  sticky?: "po" | "customer";
  hideable?: boolean;
  /** Aktifkan filter Excel-style di header (client-side pada data tersedia). */
  filterable?: boolean;
  /** Tipe sorting/filter; default text. */
  filterType?: ColumnFilterKind;
  /** Nilai teks untuk daftar unik & pencocokan filter. */
  getFilterValue?: (row: T) => string;
  /** Nilai mentah untuk sorting number/date bila berbeda dari getFilterValue. */
  getSortValue?: (row: T) => string | number | Date | null | undefined;
  defaultWidth?: number;
  minWidth?: number;
  render: (row: T) => ReactNode;
};

export type TableColumnPrefs = {
  version: number;
  order: string[];
  widths: Record<string, number>;
  hidden: string[];
};

export type ColumnHeaderFilterState = {
  /** null = semua nilai (tidak memfilter). */
  selected: string[] | null;
  sortDir: "asc" | "desc" | null;
};

import type { ReactNode } from "react";

export type ColumnDef<T> = {
  id: string;
  label: string;
  sticky?: "po" | "customer";
  hideable?: boolean;
  render: (row: T) => ReactNode;
};

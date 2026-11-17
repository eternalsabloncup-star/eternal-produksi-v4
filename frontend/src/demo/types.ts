/** Shared contracts for demo now / API later. No permanent business enum lock-in. */

export type DemoPoStatus = string;

export type PoRow = {
  id: string;
  customer: string;
  product: string;
  qty: number;
  printed: number;
  qc: number;
  branch: string;
  status: DemoPoStatus;
  machine: string;
  deadline: string;
  urgent: boolean;
  owner: string;
  sample: boolean;
  activity: string[];
};

export type PoFilters = {
  q?: string;
  status?: string;
  branch?: string;
  machine?: string;
  urgent?: boolean;
};

export type PaginationParams = {
  page: number;
  pageSize: number;
};

export type PaginatedResult<T> = {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
  source: "demo" | "api";
};

export type PoListQuery = PoFilters & PaginationParams;

export interface PoDataSource {
  readonly kind: "demo" | "api";
  list(query: PoListQuery): Promise<PaginatedResult<PoRow>>;
  getById(id: string): Promise<PoRow | null>;
  metrics(): Promise<Record<string, number>>;
}

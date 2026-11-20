/** Shared contracts for demo now / Accurate later. */

import type { PoDataOrigin, ProcessKind } from "../domain/poIdentity";

export type DemoPoStatus = string;

/** Baris item PO — identitas line terpisah dari kode barang. */
export type PoLineItem = {
  /** Identitas teknis baris (stabil). */
  lineId: string;
  /** ID baris Accurate (null untuk DEMO). */
  accurateLineId?: string | null;
  /** Kode barang Accurate/DEMO — bukan pengganti lineId. */
  code: string;
  name: string;
  qty: number;
  unit: string;
  /**
   * Nilai proses mentah dari sumber.
   * Mapping Cetak/Non-Cetak hanya via allowlist terverifikasi.
   */
  processRaw: string;
  /** Hasil mapping; null jika sumber belum terverifikasi. */
  processKind: ProcessKind | null;
  /**
   * Label tampilan Proses (kompatibilitas kolom existing).
   * Diisi dari processKind bila ada, selain itu processRaw.
   */
  process: string;
  color: string;
};

export type PoRow = {
  /**
   * Nomor PO tampilan (No. PO Accurate / DEMO).
   * Dipertahankan sebagai kunci tampilan Inbox existing (`id`).
   */
  id: string;
  /** Alias eksplisit nomor PO — sama dengan id. */
  poNo: string;
  /** Identitas teknis sumber — untuk modul berikutnya. */
  sourcePoId: string;
  /** ID header Accurate (null untuk DEMO). */
  accurateId?: string | null;
  /** Asal record: demo | accurate. Jangan dicampur dalam satu list tanpa filter. */
  origin: PoDataOrigin;
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
  /** Master Accurate-style (header Inbox). */
  poDate: string;
  paymentTerms: string;
  etaShip: string;
  seller: string;
  expedition: string;
  shipZone: string;
  recipient: string;
  address: string;
  userName: string;
  bufferLabel: string;
  notes: string;
  /** PIC alamat customer — bukan konfirmasi penerima aktual. */
  addressPicName?: string;
  /** User cetak Accurate (printUserName) — bukan creator. */
  printUserName?: string;
  /** ID pembuat Accurate (createdBy) — tanpa lookup nama. */
  createdById?: string;
  /** Prioritas dari charField4 / searchCharField4.name. */
  priorityLabel?: string;
  /** Teks total kuantitas dari dokumen sumber (tanpa konversi satuan). */
  totalQtyDisplay: string;
  itemCount: number;
  items: PoLineItem[];
  /** Gate Terima PO (backend) — pending sampai Kepala Produksi menerima. */
  acceptanceStatus?: "pending" | "accepted" | string;
  acceptedBy?: string | null;
  acceptedAt?: string | null;
  /** Gate payment dari backend (bukan status lunas keuangan). */
  paymentGate?: {
    state?: "open" | "closed" | "review" | string;
    bypass?: boolean;
    reason?: string | null;
  } | null;
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

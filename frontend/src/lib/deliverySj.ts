/**
 * Logika tampilan Jadwal Pengiriman atas data Surat Jalan nyata dari API.
 * Murni fungsi — tidak memanggil jaringan, tidak mengarang nilai yang kosong.
 */

import type {
  DeliveryNote,
  DeliveryNoteItem,
  DeliveryScheduleRow,
} from "../api/delivery";
import type { ShipDateFilterValue } from "../demo/deliverySjDemo";
import { toIsoDateString } from "./formatDate";

export const EMPTY_FIELD = "—";

/** Nilai kosong ditampilkan jelas sebagai kosong, bukan diisi asumsi. */
export function display(value: string | null | undefined): string {
  const text = (value ?? "").trim();
  return text ? text : EMPTY_FIELD;
}

export function displayBoolean(value: boolean | null | undefined): string {
  if (value === true) return "Ya";
  if (value === false) return "Tidak";
  return EMPTY_FIELD;
}

export function qtyText(value: number): string {
  return value.toLocaleString("id-ID");
}

/** Qty tidak pernah dijumlahkan lintas satuan tanpa aturan konversi Owner. */
export function qtyByUnit(items: DeliveryNoteItem[]): Record<string, number> {
  const map: Record<string, number> = {};
  for (const item of items) {
    const unit = (item.unit || "").trim() || EMPTY_FIELD;
    map[unit] = (map[unit] || 0) + Number(item.qty || 0);
  }
  return map;
}

export function formatQtyByUnit(map: Record<string, number>): string {
  const parts = Object.entries(map).map(
    ([unit, value]) => `${qtyText(value)} ${unit}`,
  );
  return parts.length ? parts.join(" + ") : EMPTY_FIELD;
}

export function rowTotalQtyDisplay(row: DeliveryScheduleRow): string {
  const map =
    row.qty_by_unit && Object.keys(row.qty_by_unit).length > 0
      ? row.qty_by_unit
      : qtyByUnit(row.items || []);
  return formatQtyByUnit(map);
}

export function matchesShipDate(
  shipDate: string | null | undefined,
  filter: ShipDateFilterValue,
): boolean {
  if (filter.kind === "all") return true;
  // Sumber bisa `YYYY-MM-DD` atau `DD/MM/YYYY`; bandingkan dalam satu format.
  const iso = toIsoDateString(shipDate);
  if (!iso) return false;
  if (filter.kind === "single") return iso === filter.date;
  return iso >= filter.start && iso <= filter.end;
}

const SEARCH_FIELDS: (keyof DeliveryNote)[] = [
  "sj_no",
  "po_no",
  "customer_po_no",
  "customer_name",
  "customer_no",
  "destination",
  "contact_info",
  "salesman_name",
  "created_by_user_name",
  "shipment",
  "ship_zone",
  "branch_name",
  "description",
  "printed_by_user",
  "packing_number",
  "accurate_status_name",
];

export function matchesSearch(row: DeliveryScheduleRow, q: string): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  for (const field of SEARCH_FIELDS) {
    const value = row[field];
    if (typeof value === "string" && value.toLowerCase().includes(needle)) {
      return true;
    }
  }
  return (row.items || []).some(
    (item) =>
      item.item_code.toLowerCase().includes(needle) ||
      item.item_name.toLowerCase().includes(needle),
  );
}

export function filterDeliveryRows(
  rows: DeliveryScheduleRow[],
  filter: ShipDateFilterValue,
  q: string,
): DeliveryScheduleRow[] {
  return rows.filter(
    (row) => matchesShipDate(row.ship_date, filter) && matchesSearch(row, q),
  );
}

export type DeliveryScheduleMetrics = {
  sjCount: number;
  qtyByUnit: Record<string, number>;
  destinationCount: number;
  loadedCount: number;
  needsReloadingCount: number;
};

/** KPI dihitung dari baris yang sedang tampil, bukan dari angka terpisah. */
export function scheduleMetrics(
  rows: DeliveryScheduleRow[],
): DeliveryScheduleMetrics {
  const destinations = new Set<string>();
  const qty: Record<string, number> = {};
  let loaded = 0;
  let needsReloading = 0;
  for (const row of rows) {
    const destination = (row.destination || "").trim();
    if (destination) destinations.add(destination);
    for (const [unit, value] of Object.entries(
      row.qty_by_unit && Object.keys(row.qty_by_unit).length > 0
        ? row.qty_by_unit
        : qtyByUnit(row.items || []),
    )) {
      qty[unit] = (qty[unit] || 0) + value;
    }
    if (row.loaded) loaded += 1;
    if (row.needs_reloading) needsReloading += 1;
  }
  return {
    sjCount: rows.length,
    qtyByUnit: qty,
    destinationCount: destinations.size,
    loadedCount: loaded,
    needsReloadingCount: needsReloading,
  };
}

export function deliveryStatusLabel(status: string): string {
  if (status === "berhasil") return "Berhasil";
  if (status === "gagal") return "Gagal";
  return "Menunggu Pengiriman";
}

export function printStatusFilterText(
  status: DeliveryScheduleRow["print_status"] | null,
): string {
  const label = status?.label?.trim();
  return label || "Belum tersedia";
}

/** Persen hanya untuk status yang angkanya sah. Kosong mengurut di akhir. */
export function printStatusSortValue(
  status: DeliveryScheduleRow["print_status"] | null,
): number | null {
  if (!status) return null;
  if (status.state === "unavailable" || status.state === "non_cetak") return null;
  if (status.percent == null || status.qty_confirmed == null || status.qty_reference == null) {
    return null;
  }
  return status.percent;
}

export function printStatusHasNumbers(
  status: DeliveryScheduleRow["print_status"] | null,
): status is NonNullable<DeliveryScheduleRow["print_status"]> & {
  qty_confirmed: number;
  qty_reference: number;
  unit: string;
  percent: number;
} {
  return Boolean(
    status &&
      status.state !== "unavailable" &&
      status.state !== "non_cetak" &&
      status.qty_confirmed != null &&
      status.qty_reference != null &&
      status.percent != null &&
      status.unit,
  );
}

export function loadingStatusLabel(row: DeliveryScheduleRow): string {
  if (row.needs_reloading) return "Perlu Loading Ulang";
  return row.loaded ? "Sudah Loading" : "Belum Loading";
}

export function revisionStatusLabel(status: string | null | undefined): string {
  if (status === "PERLU_LOADING_ULANG") return "Perlu Loading Ulang";
  if (status === "PERLU_KOREKSI_ADMINISTRASI") return "Perlu Koreksi Administrasi";
  if (status === "aktif") return "Aktif";
  return display(status);
}

export function loadingEventStatusLabel(status: string): string {
  if (status === "dibatalkan") return "Dibatalkan";
  if (status === "aktif") return "Aktif";
  return display(status);
}

/** Field yang tidak dikirim Accurate — dilaporkan apa adanya, bukan ditebak. */
export function absentFieldCount(row: DeliveryNote): number {
  return row.source_meta?.absent_fields?.length ?? 0;
}

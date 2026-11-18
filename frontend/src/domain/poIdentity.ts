/**
 * Kontrak identitas PO / item line untuk Tahap 01.
 * Modul berikutnya wajib memakai identitas ini — bukan nomor tampilan saja.
 */

import type { ProcessKind } from "./poProcessMapping";

export type PoDataOrigin = "demo" | "accurate";

/** Identitas teknis PO — stabil lintas modul. */
export type PoIdentity = {
  /** Asal data. DEMO dan Accurate tidak dicampur. */
  origin: PoDataOrigin;
  /**
   * ID teknis sumber.
   * DEMO: `demo:<poNo>`
   * Accurate (nanti): ID/dokumen Accurate asli — jangan diganti nomor tampilan.
   */
  sourcePoId: string;
  /** Nomor PO asli (tampilan) — dari Accurate bila sumber nyata. */
  poNo: string;
};

/** Identitas teknis item line. */
export type PoLineIdentity = {
  /** ID teknis baris; unik dalam sistem. */
  lineId: string;
  /** Relasi ke PO sumber. */
  sourcePoId: string;
  poNo: string;
  /** Kode barang (SKU) — tidak dijadikan pengganti lineId. */
  itemCode: string;
};

export function buildDemoSourcePoId(poNo: string): string {
  return `demo:${poNo}`;
}

/**
 * lineId DEMO stabil dari PO + kode barang.
 * Bukan untuk Accurate nyata (Accurate akan memakai ID baris sumber).
 */
export function buildDemoLineId(poNo: string, itemCode: string): string {
  return `demo:${poNo}:line:${itemCode}`;
}

export function assertSamePoIdentity(
  a: Pick<PoIdentity, "sourcePoId" | "poNo" | "origin">,
  b: Pick<PoIdentity, "sourcePoId" | "poNo" | "origin">,
): boolean {
  return (
    a.origin === b.origin &&
    a.sourcePoId === b.sourcePoId &&
    a.poNo === b.poNo
  );
}

export type { ProcessKind };

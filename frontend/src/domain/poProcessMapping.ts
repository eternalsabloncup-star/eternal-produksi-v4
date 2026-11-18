/**
 * Mapping proses sumber → Cetak / Non-Cetak.
 * Hanya nilai yang terverifikasi Owner / allowlist eksplisit.
 * Jangan menebak dari nama barang.
 * "Polos" sengaja TIDAK dipetakan sampai Owner mengonfirmasi sumber Accurate.
 */

export type ProcessKind = "Cetak" | "Non-Cetak";

/**
 * Allowlist nilai proses sumber.
 * Accurate: detailItem[].charField1 = "Cetak" | "Tidak Cetak".
 */
export const VERIFIED_PROCESS_SOURCE_MAP: Readonly<Record<string, ProcessKind>> =
  {
    Cetak: "Cetak",
    "Tidak Cetak": "Non-Cetak",
    "Non-Cetak": "Non-Cetak",
  };

export type ProcessMappingResult =
  | { ok: true; processKind: ProcessKind; processRaw: string }
  | { ok: false; processKind: null; processRaw: string; reason: string };

/** Normalisasi kunci banding (trim, case-sensitive sesuai sumber Accurate DEMO). */
export function mapProcessFromSource(
  processRaw: string | null | undefined,
): ProcessMappingResult {
  const raw = (processRaw ?? "").trim();
  if (!raw) {
    return {
      ok: false,
      processKind: null,
      processRaw: "",
      reason: "Nilai proses sumber kosong.",
    };
  }
  const hit = VERIFIED_PROCESS_SOURCE_MAP[raw];
  if (hit) {
    return { ok: true, processKind: hit, processRaw: raw };
  }
  return {
    ok: false,
    processKind: null,
    processRaw: raw,
    reason: `Nilai proses "${raw}" belum ada di allowlist terverifikasi (bukan asumsi nama barang).`,
  };
}

export function isPrintProcess(kind: ProcessKind | null | undefined): boolean {
  return kind === "Cetak";
}

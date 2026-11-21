/**
 * Label cabang untuk presentasi PWA — Master Cabang name, bukan "Cabang {id}".
 * ID tetap dipakai internal; tidak ada hardcode mapping numerik → nama.
 */

export type BranchDisplayInput = {
  /** Accurate / kanonis branch id (tetap internal). */
  branchId: string | null | undefined;
  /** Nama dari resolver Master (PO detail / settings branches). */
  branchName: string | null | undefined;
  /** True bila backend memverifikasi nama dari Master. */
  branchNameVerified?: boolean | null;
};

export type BranchDisplayResult = {
  label: string;
  /** True bila nama Master absen — jangan tampilkan ID sebagai nama. */
  missingMapping: boolean;
};

/**
 * Presentasi fail-closed terhadap nama menyesatkan.
 * - Ada nama Master → tampilkan nama saja.
 * - Ada ID tanpa nama → fallback netral + flag missingMapping.
 * - Tanpa ID → "Cabang belum tersedia".
 */
export function formatPwaBranchLabel(
  input: BranchDisplayInput,
): BranchDisplayResult {
  const id = String(input.branchId || "").trim();
  const name = String(input.branchName || "").trim();
  if (name) {
    return { label: name, missingMapping: false };
  }
  if (!id) {
    return { label: "Cabang belum tersedia", missingMapping: false };
  }
  return {
    label: "Cabang (nama Master belum terpetakan)",
    missingMapping: true,
  };
}

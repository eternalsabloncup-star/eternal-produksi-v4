/** Distingsi kosong vs gagal muat — jangan samakan LOAD_FAILED dengan DATA_EMPTY. */
export type ResourceLoadState =
  | "idle"
  | "loading"
  | "SUCCESS"
  | "DATA_EMPTY"
  | "LOAD_FAILED"
  | "FORBIDDEN"
  | "PARTIAL_LOAD";

export type SettingsResourceKey =
  | "users"
  | "roles"
  | "branches"
  | "machines"
  | "shifts"
  | "audits"
  | "accurate"
  | "masterJobs";

export function classifyListResult(
  ok: boolean,
  count: number,
  forbidden = false,
): ResourceLoadState {
  if (forbidden) return "FORBIDDEN";
  if (!ok) return "LOAD_FAILED";
  if (count === 0) return "DATA_EMPTY";
  return "SUCCESS";
}

export function loadStateMessage(state: ResourceLoadState, label: string): string {
  switch (state) {
    case "LOAD_FAILED":
      return `Gagal memuat ${label}. Ini bukan berarti data kosong.`;
    case "FORBIDDEN":
      return `Anda tidak punya hak melihat ${label}.`;
    case "DATA_EMPTY":
      return `Belum ada data ${label}.`;
    case "PARTIAL_LOAD":
      return `Sebagian ${label} gagal dimuat.`;
    default:
      return "";
  }
}

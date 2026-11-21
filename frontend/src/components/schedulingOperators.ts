import { SchedulingApiError, type SchedulingOperator } from "../api/scheduling";
import { userDisplayName } from "../lib/userDisplayName";

/** Label dropdown/tabel operasional — Master display_name (tanpa fallback email). */
export function operatorCandidateLabel(op: {
  email: string;
  display_name?: string | null;
}): string {
  return userDisplayName(op.display_name);
}

/** Kandidat dropdown: email wajib; nama kosong tetap lolos (label fallback). */
export function usableSchedulingOperators(
  items: SchedulingOperator[],
): SchedulingOperator[] {
  return items.filter((o) => Boolean((o.email || "").trim()));
}

export type OperatorsLoadState =
  | { status: "idle" | "loading" }
  | { status: "ok"; items: SchedulingOperator[] }
  | { status: "empty" }
  | { status: "denied"; message: string }
  | { status: "error"; message: string };

export function operatorsLoadFromSuccess(
  items: SchedulingOperator[],
): OperatorsLoadState {
  const usable = usableSchedulingOperators(items);
  if (usable.length === 0) return { status: "empty" };
  return { status: "ok", items: usable };
}

export function operatorsLoadFromError(err: unknown): OperatorsLoadState {
  if (err instanceof SchedulingApiError && err.status === 403) {
    return {
      status: "denied",
      message:
        err.message ||
        "Izin ditolak: daftar operator hanya untuk peran penjadwalan.",
    };
  }
  return {
    status: "error",
    message:
      err instanceof Error
        ? err.message
        : "Gagal memuat daftar operator Master.",
  };
}

export function operatorsBlockerMessage(
  state: OperatorsLoadState,
): string | null {
  if (state.status === "denied") return state.message;
  if (state.status === "error") return state.message;
  if (state.status === "empty") {
    return (
      "Tidak ada operator aktif di Master Pengguna " +
      "(peran operasional Ops = operator, status aktif)."
    );
  }
  return null;
}

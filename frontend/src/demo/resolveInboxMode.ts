export type PoSourceMode = "demo" | "api";

/**
 * Mode sumber Inbox.
 * - eksplisit api|accurate → api
 * - eksplisit demo → demo
 * - kosong: production build → api (hindari default DEMO diam-diam);
 *   development → demo
 */
export function resolveInboxPoSourceMode(
  envValue?: string,
  options?: { production?: boolean },
): PoSourceMode {
  const raw = (envValue ?? "").trim().toLowerCase();
  if (raw === "api" || raw === "accurate") return "api";
  if (raw === "demo") return "demo";
  const isProd =
    options?.production ??
    (typeof import.meta !== "undefined" &&
      Boolean((import.meta as ImportMeta & { env?: { PROD?: boolean } }).env?.PROD));
  return isProd ? "api" : "demo";
}

/** Mode indikator shell per route. */
export type ShellDataMode = "api" | "demo" | "shell";

export function resolveShellDataMode(
  pathname: string,
  inboxMode: PoSourceMode,
): ShellDataMode {
  if (pathname.startsWith("/app/inbox")) {
    return inboxMode === "api" ? "api" : "demo";
  }
  if (pathname.startsWith("/app/control")) {
    return "demo";
  }
  return "shell";
}

export function shellModeBadgeLabel(mode: ShellDataMode): string | null {
  if (mode === "api") return "Accurate · MongoDB V4";
  if (mode === "demo") return "● DEMO";
  return null;
}

export function shellModeFooterHint(mode: ShellDataMode): string {
  if (mode === "api") {
    return "Eternal Production V4 · Inbox Accurate read-only dari MongoDB V4 (bukan DEMO).";
  }
  if (mode === "demo") {
    return "Eternal Production V4 · Modul ini memakai data DEMO browser — tidak menulis MongoDB.";
  }
  return "Eternal Production V4 · Kerangka navigasi — modul belum terhubung data produksi.";
}

export function shellSidefootCopy(mode: ShellDataMode): { title: string; sub: string } {
  if (mode === "api") {
    return {
      title: "Inbox · Accurate",
      sub: "MongoDB V4 · read-only",
    };
  }
  if (mode === "demo") {
    return {
      title: "UI Shell · DEMO",
      sub: "Simulasi · tidak menulis MongoDB",
    };
  }
  return {
    title: "UI Shell · Kerangka",
    sub: "Modul belum terimplementasi",
  };
}

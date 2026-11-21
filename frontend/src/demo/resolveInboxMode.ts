export type PoSourceMode = "demo" | "api";

/**
 * Mode sumber Inbox.
 * - eksplisit api|accurate → api
 * - eksplisit demo → demo
 * - kosong → api (production & Cursor preview memakai panel API yang sama;
 *   DEMO hanya jika dipaksa `VITE_INBOX_PO_SOURCE=demo`)
 */
export function resolveInboxPoSourceMode(
  envValue?: string,
  _options?: { production?: boolean },
): PoSourceMode {
  const raw = (envValue ?? "").trim().toLowerCase();
  if (raw === "api" || raw === "accurate") return "api";
  if (raw === "demo") return "demo";
  return "api";
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
  // Master/Pengaturan membaca MongoDB V4 pada kedua mode.
  if (pathname.startsWith("/app/settings")) {
    return "api";
  }
  // Dashboard dan Laporan membaca read model MongoDB V4 pada kedua mode.
  if (pathname.startsWith("/app/dashboard") || pathname.startsWith("/app/reports")) {
    return "api";
  }
  // Modul operasional mengikuti mode Inbox: API nyata atau DEMO lokal.
  const operationalPrefixes = [
    "/app/tasks",
    "/app/schedule",
    "/app/delivery",
    "/app/production-flow",
    "/app/buffer-stock",
  ];
  if (operationalPrefixes.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    return inboxMode === "api" ? "api" : "demo";
  }
  // Alias legacy: /control dan /samples diarahkan ke schedule/buffer (App.tsx).
  // Mode mengikuti sumber Inbox, bukan DEMO paksa.
  if (pathname.startsWith("/app/control") || pathname.startsWith("/app/samples")) {
    return inboxMode === "api" ? "api" : "demo";
  }
  // /app/qc dialihkan ke tugas Helper — indikator mengikuti operational tasks.
  if (pathname.startsWith("/app/qc")) {
    return inboxMode === "api" ? "api" : "demo";
  }
  return "shell";
}

export function shellModeBadgeLabel(mode: ShellDataMode): string | null {
  if (mode === "api") return "Accurate";
  if (mode === "demo") return "● DEMO";
  return null;
}

export function shellModeFooterHint(mode: ShellDataMode): string {
  if (mode === "api") {
    return "Eternal Production V4 · Data operasional (bukan DEMO).";
  }
  if (mode === "demo") {
    return "Eternal Production V4 · Modul ini memakai data DEMO browser — tidak menulis data produksi.";
  }
  return "Eternal Production V4 · Kerangka navigasi — modul belum terhubung data produksi.";
}

export function shellSidefootCopy(mode: ShellDataMode): { title: string; sub: string } {
  if (mode === "api") {
    return {
      title: "Inbox · Accurate",
      sub: "Accurate · read-only",
    };
  }
  if (mode === "demo") {
    return {
      title: "UI Shell · DEMO",
      sub: "Simulasi · tidak menulis data produksi",
    };
  }
  return {
    title: "UI Shell · Kerangka",
    sub: "Modul belum terimplementasi",
  };
}

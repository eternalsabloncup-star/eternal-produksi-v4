import type { NavIconName } from "./icons";

export type AppNavId =
  | "dashboard"
  | "inbox"
  | "production-flow"
  | "schedule"
  | "tracking"
  | "tasks"
  | "buffer-stock"
  | "qc"
  | "delivery"
  | "reports"
  | "settings";

export type AppNavItem = {
  id: AppNavId;
  path: string;
  icon: NavIconName;
  label: string;
  /** Warna latar kotak ikon (sidebar). */
  box: string;
  /** Warna simbol ikon. */
  fg: string;
  showcase?: boolean;
};

export type TeamTaskRoleId =
  | "production-head"
  | "designer"
  | "helper"
  | "screener"
  | "operator"
  | "driver";

export type TeamTaskRole = {
  id: TeamTaskRoleId;
  path: string;
  icon: NavIconName;
  label: string;
  box: string;
  /** Warna ikon flyout — visual saja, bukan aturan bisnis. */
  color: string;
};

/** Enam peran submenu Tugas Tim (EPV4-005). */
export const TEAM_TASK_ROLES: TeamTaskRole[] = [
  {
    id: "production-head",
    path: "/app/tasks/production-head",
    icon: "crown",
    label: "Kepala Produksi",
    box: "#5a4a18",
    color: "#e8c547",
  },
  {
    id: "designer",
    path: "/app/tasks/designer",
    icon: "pen-tool",
    label: "Designer",
    box: "#1e4a62",
    color: "#82C7EC",
  },
  {
    id: "helper",
    path: "/app/tasks/helper",
    icon: "wrench",
    label: "Helper",
    box: "#624836",
    color: "#F5B88D",
  },
  {
    id: "screener",
    path: "/app/tasks/screener",
    icon: "scan",
    label: "Screener",
    box: "#463D63",
    color: "#BEA8F5",
  },
  {
    id: "operator",
    path: "/app/tasks/operator",
    icon: "cog",
    label: "Operator",
    box: "#16554c",
    color: "#76D2C0",
  },
  {
    id: "driver",
    path: "/app/tasks/driver",
    icon: "car",
    label: "Driver",
    box: "#663E53",
    color: "#F1A8C8",
  },
];

/** Entri Ringkasan di flyout Tugas Tim. */
export const TEAM_TASKS_SUMMARY = {
  path: "/app/tasks",
  icon: "circle-dot" as const satisfies NavIconName,
  label: "Ringkasan",
  box: "#225548",
  color: "#76D2C0",
};

/**
 * Menu sidebar utama (icon-only).
 * "Hasil Produksi & QC" sengaja tidak disertakan di navigasi sidebar.
 */
export const APP_NAV: AppNavItem[] = [
  {
    id: "dashboard",
    path: "/app/dashboard",
    icon: "layout-dashboard",
    label: "Dashboard",
    box: "#225548",
    fg: "#76D2C0",
  },
  {
    id: "inbox",
    path: "/app/inbox",
    icon: "inbox",
    label: "Inbox PO",
    box: "#59482C",
    fg: "#F4C775",
    showcase: true,
  },
  {
    id: "schedule",
    path: "/app/schedule",
    icon: "chart-no-axes-combined",
    label: "Jadwal Produksi",
    box: "#31583E",
    fg: "#94D4A2",
    showcase: true,
  },
  {
    id: "production-flow",
    path: "/app/production-flow",
    icon: "workflow",
    label: "Alur Produksi",
    box: "#1F4F58",
    fg: "#7ED4C8",
  },
  {
    id: "tasks",
    path: "/app/tasks",
    icon: "clipboard-list",
    label: "Tugas Tim",
    box: "#2E4D66",
    fg: "#82C7EC",
  },
  {
    id: "buffer-stock",
    path: "/app/buffer-stock",
    icon: "warehouse",
    label: "Buffer Stock",
    box: "#4A3F2A",
    fg: "#E2C98A",
  },
  {
    id: "delivery",
    path: "/app/delivery",
    icon: "truck",
    label: "Jadwal Pengiriman",
    box: "#624836",
    fg: "#F5B88D",
  },
  {
    id: "reports",
    path: "/app/reports",
    icon: "chart-column-increasing",
    label: "Laporan",
    box: "#415630",
    fg: "#B4D98C",
  },
  {
    id: "settings",
    path: "/app/settings",
    icon: "settings",
    label: "Pengaturan",
    box: "#43504E",
    fg: "#D0D8DE",
  },
];

/** Metadata header untuk route yang tidak ada di sidebar. */
const HIDDEN_NAV: Partial<Record<AppNavId, AppNavItem>> = {
  qc: {
    id: "qc",
    path: "/app/qc",
    icon: "badge-check",
    label: "Hasil Produksi & QC",
    box: "#31583E",
    fg: "#94D4A2",
  },
  /** Live Tracking dikelola dari kolom Inbox PO — bukan menu sidebar. */
  tracking: {
    id: "tracking",
    path: "/app/tracking",
    icon: "truck",
    label: "Live Tracking",
    box: "#1F4F58",
    fg: "#7ED4C8",
  },
};

export function teamTaskRoleByPath(pathname: string): TeamTaskRole | undefined {
  return TEAM_TASK_ROLES.find((r) => pathname === r.path);
}

export function isTasksSectionPath(pathname: string): boolean {
  return pathname === "/app/tasks" || pathname.startsWith("/app/tasks/");
}

export function navByPath(pathname: string): AppNavItem {
  const role = teamTaskRoleByPath(pathname);
  if (role) {
    return {
      id: "tasks",
      path: role.path,
      icon: role.icon,
      label: role.label,
      box: role.box,
      fg: role.color,
    };
  }
  if (pathname === "/app/qc" || pathname.startsWith("/app/qc/")) {
    return HIDDEN_NAV.qc!;
  }
  if (pathname === "/app/tracking" || pathname.startsWith("/app/tracking/")) {
    return HIDDEN_NAV.tracking!;
  }
  const hit = APP_NAV.find(
    (n) => pathname === n.path || pathname.startsWith(n.path + "/"),
  );
  return hit ?? APP_NAV.find((n) => n.id === "schedule")!;
}

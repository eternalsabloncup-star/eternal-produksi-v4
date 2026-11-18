import type { NavIconName } from "./icons";

export type AppNavId =
  | "dashboard"
  | "inbox"
  | "control"
  | "schedule"
  | "tasks"
  | "samples"
  | "qc"
  | "delivery"
  | "reports"
  | "settings";

export type AppNavItem = {
  id: AppNavId;
  path: string;
  icon: NavIconName;
  label: string;
  /** Warna latar kotak ikon (sidebar rail). */
  box: string;
  /** Warna simbol ikon. */
  fg: string;
  showcase?: boolean;
};

/**
 * Menu Tahap 01 (struktur classic main) + visual hijau rail.
 * Tidak menyertakan modul Tahap 02 (Alur Produksi / Buffer Stock / peran Tugas Tim).
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
    id: "control",
    path: "/app/control",
    icon: "chart-no-axes-combined",
    label: "Kontrol Produksi",
    box: "#31583E",
    fg: "#94D4A2",
    showcase: true,
  },
  {
    id: "schedule",
    path: "/app/schedule",
    icon: "calendar-days",
    label: "Jadwal Produksi",
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
    id: "samples",
    path: "/app/samples",
    icon: "badge-check",
    label: "Approval Sampel",
    box: "#4A3F2A",
    fg: "#E2C98A",
  },
  {
    id: "qc",
    path: "/app/qc",
    icon: "circle-dot",
    label: "Hasil Produksi & QC",
    box: "#16554c",
    fg: "#76D2C0",
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

export function navByPath(pathname: string): AppNavItem {
  const hit = APP_NAV.find(
    (n) => pathname === n.path || pathname.startsWith(n.path + "/"),
  );
  return hit ?? APP_NAV.find((n) => n.id === "control")!;
}

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
  icon: string;
  label: string;
  showcase?: boolean;
};

export const APP_NAV: AppNavItem[] = [
  { id: "dashboard", path: "/app/dashboard", icon: "◫", label: "Dashboard" },
  { id: "inbox", path: "/app/inbox", icon: "▤", label: "Inbox PO", showcase: true },
  {
    id: "control",
    path: "/app/control",
    icon: "◉",
    label: "Kontrol Produksi",
    showcase: true,
  },
  { id: "schedule", path: "/app/schedule", icon: "▦", label: "Jadwal Produksi" },
  { id: "tasks", path: "/app/tasks", icon: "☷", label: "Tugas Tim" },
  { id: "samples", path: "/app/samples", icon: "▣", label: "Approval Sampel" },
  { id: "qc", path: "/app/qc", icon: "✓", label: "Hasil Produksi & QC" },
  { id: "delivery", path: "/app/delivery", icon: "⇢", label: "Jadwal Pengiriman" },
  { id: "reports", path: "/app/reports", icon: "▥", label: "Laporan" },
  { id: "settings", path: "/app/settings", icon: "⚙", label: "Pengaturan" },
];

export function navByPath(pathname: string): AppNavItem {
  const hit = APP_NAV.find((n) => pathname === n.path || pathname.startsWith(n.path + "/"));
  return hit ?? APP_NAV.find((n) => n.id === "control")!;
}

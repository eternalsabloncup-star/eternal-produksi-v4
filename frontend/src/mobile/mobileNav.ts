/** Empat tab navigasi bawah PWA Operasional Mobile. */

export type MobileOpsTabId =
  | "tasks"
  | "schedule"
  | "delivery"
  | "monitoring";

export type MobileOpsTab = {
  id: MobileOpsTabId;
  path: string;
  label: string;
  short: string;
};

export const MOBILE_OPS_BASE = "/app/m";

export const MOBILE_OPS_TABS: MobileOpsTab[] = [
  {
    id: "tasks",
    path: `${MOBILE_OPS_BASE}/tasks`,
    label: "Tugas Saya",
    short: "Tugas",
  },
  {
    id: "schedule",
    path: `${MOBILE_OPS_BASE}/schedule`,
    label: "Jadwal Produksi",
    short: "Produksi",
  },
  {
    id: "delivery",
    path: `${MOBILE_OPS_BASE}/delivery`,
    label: "Jadwal Pengiriman",
    short: "Kirim",
  },
  {
    id: "monitoring",
    path: `${MOBILE_OPS_BASE}/monitoring`,
    label: "Monitoring",
    short: "Monitor",
  },
];

export function mobileTabByPath(pathname: string): MobileOpsTab {
  const hit = MOBILE_OPS_TABS.find(
    (t) => pathname === t.path || pathname.startsWith(t.path + "/"),
  );
  return hit ?? MOBILE_OPS_TABS[0];
}

export function isMobileOpsPath(pathname: string): boolean {
  return (
    pathname === MOBILE_OPS_BASE || pathname.startsWith(`${MOBILE_OPS_BASE}/`)
  );
}

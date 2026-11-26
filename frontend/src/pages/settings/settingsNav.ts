import type { NavIconName } from "../../app/icons";

/** Settings Center — leaf tabs (fitur lama tetap ada sebagai sub-bagian). */
export type SettingsTabId =
  | "overview"
  | "users"
  | "roles-catalog"
  | "module-perms"
  | "ops-grants"
  | "task-types"
  | "task-assign"
  | "jobs-board"
  | "branches"
  | "machines"
  | "shifts"
  | "products"
  | "product-screen"
  | "accurate"
  | "workflow"
  | "audit";

export type SettingsNavItem = {
  id: SettingsTabId;
  label: string;
  icon: NavIconName;
  description: string;
};

export type SettingsNavSection = {
  id: string;
  label: string;
  items: SettingsNavItem[];
};

/** Lima menu utama — leaf tetap ada untuk preservasi fitur. */
export const SETTINGS_NAV_SECTIONS: SettingsNavSection[] = [
  {
    id: "access",
    label: "Pengguna & Akses",
    items: [
      {
        id: "overview",
        label: "Ringkasan",
        icon: "layout-dashboard",
        description: "Gambaran cepat dan pintasan ke menu pengaturan.",
      },
      {
        id: "users",
        label: "Pengguna",
        icon: "users",
        description:
          "Pilih pengguna → peran sistem → cabang → lihat akses efektif → simpan.",
      },
      {
        id: "roles-catalog",
        label: "Jabatan / Peran Sistem",
        icon: "shield-check",
        description:
          "Peran sistem = hak membuka dan mengelola fitur aplikasi (bukan peran kerja).",
      },
      {
        id: "module-perms",
        label: "Hak Akses Menu",
        icon: "shield-check",
        description: "Centang menu/fitur per peran sistem. Default: tidak ada hak.",
      },
    ],
  },
  {
    id: "jobs",
    label: "Pekerjaan & Penugasan",
    items: [
      {
        id: "jobs-board",
        label: "Papan Konfigurasi",
        icon: "clipboard-list",
        description:
          "Satu layar: jenis pekerjaan, penanggung jawab, izin, dan status kesiapan.",
      },
      {
        id: "task-types",
        label: "Jenis Pekerjaan",
        icon: "clipboard-list",
        description: "Daftar dan tambah jenis pekerjaan.",
      },
      {
        id: "task-assign",
        label: "Penanggung Jawab",
        icon: "users",
        description: "Peran kerja yang bertanggung jawab per jenis pekerjaan.",
      },
      {
        id: "ops-grants",
        label: "Izin Pekerjaan",
        icon: "clipboard-list",
        description:
          "Aksi yang boleh dilakukan peran kerja. Tidak otomatis mengubah penanggung jawab.",
      },
    ],
  },
  {
    id: "org",
    label: "Cabang & Produksi",
    items: [
      {
        id: "branches",
        label: "Cabang",
        icon: "building-2",
        description: "Master cabang dan tautan Accurate Online.",
      },
      {
        id: "machines",
        label: "Mesin",
        icon: "cog",
        description: "Mesin produksi per cabang (tanpa data dummy).",
      },
      {
        id: "shifts",
        label: "Shift / Jam Kerja",
        icon: "clock-3",
        description: "Master shift dan jam kerja.",
      },
    ],
  },
  {
    id: "products",
    label: "Produk",
    items: [
      {
        id: "products",
        label: "Master Produk",
        icon: "package",
        description: "Daftar produk dan atur ukuran screen di detail produk.",
      },
      {
        id: "product-screen",
        label: "Ukuran Screen",
        icon: "package",
        description: "Form khusus ukuran screen (sama data Master Produk).",
      },
    ],
  },
  {
    id: "system",
    label: "Sistem & Riwayat",
    items: [
      {
        id: "accurate",
        label: "Accurate Online",
        icon: "plug-zap",
        description: "Status koneksi Accurate (tanpa menampilkan rahasia).",
      },
      {
        id: "workflow",
        label: "Aturan Workflow",
        icon: "workflow",
        description: "Status aturan alur kerja (hanya tampilan).",
      },
      {
        id: "audit",
        label: "Riwayat Perubahan",
        icon: "history",
        description: "Catatan audit pengaturan (hanya ditambah).",
      },
    ],
  },
];

export const SETTINGS_NAV_ITEMS: SettingsNavItem[] = SETTINGS_NAV_SECTIONS.flatMap(
  (s) => s.items,
);

export function navItemById(id: SettingsTabId): SettingsNavItem {
  return SETTINGS_NAV_ITEMS.find((x) => x.id === id) ?? SETTINGS_NAV_ITEMS[0];
}

/** Tabs that carry local drafts requiring explicit save / discard confirm. */
export const SETTINGS_DRAFT_TABS: SettingsTabId[] = [
  "users",
  "roles-catalog",
  "module-perms",
  "ops-grants",
  "task-types",
  "task-assign",
  "jobs-board",
  "branches",
  "machines",
  "shifts",
  "products",
  "product-screen",
];

export type JobConfigStatus =
  | "SIAP"
  | "BELUM_ADA_PENANGGUNG_JAWAB"
  | "IZIN_BELUM_LENGKAP"
  | "BELUM_ADA_PENGGUNA_AKTIF"
  | "BELUM_TERVERIFIKASI";

export function jobConfigStatusLabel(status: JobConfigStatus): string {
  switch (status) {
    case "SIAP":
      return "Siap";
    case "BELUM_ADA_PENANGGUNG_JAWAB":
      return "Belum ada penanggung jawab";
    case "IZIN_BELUM_LENGKAP":
      return "Izin belum lengkap";
    case "BELUM_ADA_PENGGUNA_AKTIF":
      return "Belum ada pengguna aktif";
    case "BELUM_TERVERIFIKASI":
      return "Belum terverifikasi";
  }
}

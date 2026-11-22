import type { NavIconName } from "../../app/icons";

/** Settings Center V2 — leaf tabs (Ringkasan = landing). */
export type SettingsTabId =
  | "overview"
  | "users"
  | "roles-catalog"
  | "module-perms"
  | "ops-grants"
  | "task-types"
  | "task-assign"
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

/** Lima kelompok utama — leaf tetap transaksi terpisah. */
export const SETTINGS_NAV_SECTIONS: SettingsNavSection[] = [
  {
    id: "access",
    label: "Pengguna & Hak Akses",
    items: [
      {
        id: "overview",
        label: "Ringkasan",
        icon: "layout-dashboard",
        description: "Halaman awal pengaturan dan pintasan menu.",
      },
      {
        id: "users",
        label: "Pengguna",
        icon: "users",
        description: "Buat akun, ubah data, dan aktifkan atau nonaktifkan pengguna.",
      },
      {
        id: "roles-catalog",
        label: "Jabatan / Peran Sistem",
        icon: "shield-check",
        description: "Tambah dan kelola peran sistem (bukan peran kerja produksi).",
      },
      {
        id: "module-perms",
        label: "Hak Akses Menu",
        icon: "shield-check",
        description: "Tentukan menu dan fitur yang boleh diakses tiap peran sistem.",
      },
      {
        id: "ops-grants",
        label: "Izin Pekerjaan",
        icon: "clipboard-list",
        description: "Tentukan aksi yang boleh dilakukan peran kerja pada jenis pekerjaan.",
      },
    ],
  },
  {
    id: "ops",
    label: "Pengaturan Operasional",
    items: [
      {
        id: "task-types",
        label: "Jenis Pekerjaan",
        icon: "clipboard-list",
        description: "Daftar jenis pekerjaan dan penambahan jenis manual.",
      },
      {
        id: "task-assign",
        label: "Pembagian Tugas",
        icon: "users",
        description: "Tentukan peran kerja penanggung jawab tiap jenis pekerjaan.",
      },
      {
        id: "branches",
        label: "Cabang",
        icon: "building-2",
        description: "Master cabang dan tautan ke Accurate Online.",
      },
      {
        id: "machines",
        label: "Mesin",
        icon: "cog",
        description: "Master mesin produksi per cabang.",
      },
      {
        id: "shifts",
        label: "Jam Kerja",
        icon: "clock-3",
        description: "Master shift dan jam kerja.",
      },
    ],
  },
  {
    id: "products",
    label: "Daftar Produk",
    items: [
      {
        id: "products",
        label: "Produk",
        icon: "package",
        description: "Daftar produk dari Master Produk yang sama.",
      },
      {
        id: "product-screen",
        label: "Ukuran Screen",
        icon: "package",
        description: "Atur ukuran screen pada data produk yang ada.",
      },
    ],
  },
  {
    id: "system",
    label: "Koneksi & Aturan Sistem",
    items: [
      {
        id: "accurate",
        label: "Accurate Online",
        icon: "plug-zap",
        description: "Status koneksi Accurate (tanpa menampilkan rahasia).",
      },
      {
        id: "workflow",
        label: "Aturan Alur Kerja",
        icon: "workflow",
        description: "Status aturan alur kerja (hanya tampilan).",
      },
    ],
  },
  {
    id: "history",
    label: "Riwayat Perubahan",
    items: [
      {
        id: "audit",
        label: "Catatan Perubahan",
        icon: "history",
        description: "Riwayat perubahan pengaturan (hanya ditambah, tidak dihapus).",
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
  "branches",
  "machines",
  "shifts",
  "product-screen",
];

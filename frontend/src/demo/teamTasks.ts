import type { TeamTaskRoleId } from "../app/nav";
import { getRoleDoneCounts } from "./teamTaskWork";

/**
 * Fixture tugas DEMO per peran — selesai/belum eksplisit.
 * Frontend only; bukan assignment operasional.
 */

export type TeamTaskRow = {
  id: string;
  title: string;
  poRef: string;
  status: string;
  due: string;
  /** Penanda selesai eksplisit untuk badge. */
  done: boolean;
};

export type TeamTaskFilters = {
  q?: string;
  status?: string;
};

export const TEAM_TASK_STATUS_OPTIONS = ["Baru", "Berjalan", "Selesai"] as const;

type Seed = Omit<TeamTaskRow, "id"> & { roleId: TeamTaskRoleId };

const SEED: Seed[] = [
  // Kepala Produksi
  {
    roleId: "production-head",
    title: "Review alokasi Qty DEMO-TASK-PO-01",
    poRef: "DEMO-TASK-PO-01",
    status: "Berjalan",
    due: "2026-03-12",
    done: false,
  },
  {
    roleId: "production-head",
    title: "Cek sisa Qty DEMO-TASK-PO-02",
    poRef: "DEMO-TASK-PO-02",
    status: "Baru",
    due: "2026-03-13",
    done: false,
  },
  {
    roleId: "production-head",
    title: "Rekap alokasi minggu lalu (SIM)",
    poRef: "DEMO-TASK-PO-00",
    status: "Selesai",
    due: "2026-03-01",
    done: true,
  },
  // Designer
  {
    roleId: "designer",
    title: "Draft motif A DEMO-TASK-PO-04",
    poRef: "DEMO-TASK-PO-04",
    status: "Berjalan",
    due: "2026-03-10",
    done: false,
  },
  {
    roleId: "designer",
    title: "Revisi logo DEMO-TASK-PO-05",
    poRef: "DEMO-TASK-PO-05",
    status: "Baru",
    due: "2026-03-11",
    done: false,
  },
  {
    roleId: "designer",
    title: "Final artwork selesai (SIM)",
    poRef: "DEMO-TASK-PO-03",
    status: "Selesai",
    due: "2026-03-02",
    done: true,
  },
  {
    roleId: "designer",
    title: "Upload layout DEMO-TASK-PO-06",
    poRef: "DEMO-TASK-PO-06",
    status: "Baru",
    due: "2026-03-14",
    done: false,
  },
  // Helper
  {
    roleId: "helper",
    title: "Siapkan cup DEMO-TASK-PO-07",
    poRef: "DEMO-TASK-PO-07",
    status: "Berjalan",
    due: "2026-03-12",
    done: false,
  },
  {
    roleId: "helper",
    title: "Rapikan area persiapan (SIM)",
    poRef: "DEMO-TASK-PO-07",
    status: "Selesai",
    due: "2026-03-05",
    done: true,
  },
  {
    roleId: "helper",
    title: "Bantu loading material DEMO-TASK-PO-08",
    poRef: "DEMO-TASK-PO-08",
    status: "Baru",
    due: "2026-03-15",
    done: false,
  },
  // Screener
  {
    roleId: "screener",
    title: "Screen warna hijau DEMO-TASK-PO-09",
    poRef: "DEMO-TASK-PO-09",
    status: "Berjalan",
    due: "2026-03-11",
    done: false,
  },
  {
    roleId: "screener",
    title: "Screen motif stripe selesai (SIM)",
    poRef: "DEMO-TASK-PO-10",
    status: "Selesai",
    due: "2026-03-04",
    done: true,
  },
  {
    roleId: "screener",
    title: "Cek screen DEMO-TASK-PO-11",
    poRef: "DEMO-TASK-PO-11",
    status: "Baru",
    due: "2026-03-16",
    done: false,
  },
  {
    roleId: "screener",
    title: "Perbaiki screen retak DEMO-TASK-PO-09",
    poRef: "DEMO-TASK-PO-09",
    status: "Baru",
    due: "2026-03-12",
    done: false,
  },
  // Operator
  {
    roleId: "operator",
    title: "Cetak batch A DEMO-TASK-PO-12",
    poRef: "DEMO-TASK-PO-12",
    status: "Berjalan",
    due: "2026-03-13",
    done: false,
  },
  {
    roleId: "operator",
    title: "Cetak batch B DEMO-TASK-PO-13",
    poRef: "DEMO-TASK-PO-13",
    status: "Baru",
    due: "2026-03-14",
    done: false,
  },
  {
    roleId: "operator",
    title: "Selesai lot uji (SIM)",
    poRef: "DEMO-TASK-PO-12",
    status: "Selesai",
    due: "2026-03-03",
    done: true,
  },
  {
    roleId: "operator",
    title: "Setup mesin Demo-02 DEMO-TASK-PO-14",
    poRef: "DEMO-TASK-PO-14",
    status: "Baru",
    due: "2026-03-15",
    done: false,
  },
  // Driver
  {
    roleId: "driver",
    title: "Kirim DEMO-TASK-SJ-01",
    poRef: "DEMO-TASK-PO-15",
    status: "Berjalan",
    due: "2026-03-16",
    done: false,
  },
  {
    roleId: "driver",
    title: "Pickup gudang selesai (SIM)",
    poRef: "DEMO-TASK-PO-16",
    status: "Selesai",
    due: "2026-03-06",
    done: true,
  },
  {
    roleId: "driver",
    title: "Antre loading DEMO-TASK-SJ-02",
    poRef: "DEMO-TASK-PO-17",
    status: "Baru",
    due: "2026-03-17",
    done: false,
  },
];

const ALL: TeamTaskRow[] = SEED.map((s, i) => ({
  id: `DEMO-TASK-${String(i + 1).padStart(3, "0")}`,
  title: s.title,
  poRef: s.poRef,
  status: s.status,
  due: s.due,
  done: s.done,
  // role stored via parallel map
}));

const ROLE_BY_ID = new Map<string, TeamTaskRoleId>();
SEED.forEach((s, i) => {
  ROLE_BY_ID.set(ALL[i].id, s.roleId);
});

export function listTeamTasks(
  roleId: string,
  filters: TeamTaskFilters,
): { items: TeamTaskRow[]; total: number } {
  let items = ALL.filter((row) => ROLE_BY_ID.get(row.id) === roleId);
  const q = filters.q?.trim().toLowerCase();
  if (q) {
    items = items.filter(
      (r) =>
        r.title.toLowerCase().includes(q) ||
        r.poRef.toLowerCase().includes(q) ||
        r.id.toLowerCase().includes(q),
    );
  }
  if (filters.status) {
    items = items.filter((r) => r.status === filters.status);
  }
  return { items, total: items.length };
}

/** Badge submenu = jumlah Belum Dikerjakan per sumber data role. */
export function countOpenTeamTasks(roleId: string): number {
  return getRoleDoneCounts(roleId as TeamTaskRoleId).open;
}

export function getOpenTeamTaskCounts(): Record<TeamTaskRoleId, number> {
  const roles: TeamTaskRoleId[] = [
    "production-head",
    "designer",
    "helper",
    "screener",
    "operator",
    "driver",
  ];
  const out = {} as Record<TeamTaskRoleId, number>;
  for (const id of roles) out[id] = countOpenTeamTasks(id);
  return out;
}

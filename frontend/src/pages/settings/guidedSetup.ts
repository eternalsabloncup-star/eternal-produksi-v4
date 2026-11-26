import type { SettingsTabId } from "./settingsNav";
import type { ResourceLoadState } from "./loadState";
import type { JobConfigStatus } from "./settingsNav";

export type GuideStepStatus =
  | "BELUM_DIATUR"
  | "PERLU_DILENGKAPI"
  | "SIAP"
  | "BELUM_TERVERIFIKASI";

export type GuideStep = {
  id: string;
  title: string;
  status: GuideStepStatus;
  detail: string;
  actionLabel: string;
  targetTab: SettingsTabId;
};

export function guideStatusLabel(status: GuideStepStatus): string {
  switch (status) {
    case "BELUM_DIATUR":
      return "Belum diatur";
    case "PERLU_DILENGKAPI":
      return "Perlu dilengkapi";
    case "SIAP":
      return "Siap";
    case "BELUM_TERVERIFIKASI":
      return "Belum terverifikasi";
  }
}

function fromLoad(
  load: ResourceLoadState | undefined,
  count: number,
  emptyIsBelum = true,
): GuideStepStatus {
  if (load === "LOAD_FAILED" || load === "FORBIDDEN" || load === "loading" || load === "idle") {
    return "BELUM_TERVERIFIKASI";
  }
  if (count === 0) return emptyIsBelum ? "BELUM_DIATUR" : "PERLU_DILENGKAPI";
  return "SIAP";
}

/** Build guided steps from real persisted counts + load states — no dummy data. */
export function buildGuidedSetupSteps(args: {
  rolesCount: number;
  usersCount: number;
  usersWithBranch: number;
  machinesCount: number;
  shiftsCount: number;
  jobStatuses: JobConfigStatus[];
  load: {
    roles?: ResourceLoadState;
    users?: ResourceLoadState;
    branches?: ResourceLoadState;
    machines?: ResourceLoadState;
    shifts?: ResourceLoadState;
    masterJobs?: ResourceLoadState;
  };
}): GuideStep[] {
  const jobsLoad = args.load.masterJobs;
  let jobsStatus: GuideStepStatus = "BELUM_TERVERIFIKASI";
  let jobsDetail = "Status pekerjaan belum terverifikasi.";
  if (jobsLoad === "SUCCESS" || jobsLoad === "DATA_EMPTY") {
    if (args.jobStatuses.length === 0) {
      jobsStatus = "BELUM_DIATUR";
      jobsDetail = "Belum ada jenis pekerjaan.";
    } else if (args.jobStatuses.every((s) => s === "SIAP")) {
      jobsStatus = "SIAP";
      jobsDetail = "Semua jenis pekerjaan berstatus siap.";
    } else if (args.jobStatuses.some((s) => s === "BELUM_TERVERIFIKASI")) {
      jobsStatus = "BELUM_TERVERIFIKASI";
      jobsDetail = "Sebagian status pekerjaan belum terverifikasi.";
    } else {
      jobsStatus = "PERLU_DILENGKAPI";
      jobsDetail = "Ada jenis pekerjaan yang belum lengkap (izin / penanggung jawab / pengguna).";
    }
  }

  const usersStatus = fromLoad(args.load.users, args.usersCount);
  const branchStatus =
    args.load.users === "LOAD_FAILED" || args.load.branches === "LOAD_FAILED"
      ? "BELUM_TERVERIFIKASI"
      : args.usersCount === 0
        ? "BELUM_DIATUR"
        : args.usersWithBranch === args.usersCount && args.usersCount > 0
          ? "SIAP"
          : "PERLU_DILENGKAPI";

  return [
    {
      id: "ops-config",
      title: "1. Atur izin & jenis pekerjaan",
      status: jobsStatus,
      detail: jobsDetail,
      actionLabel: "Atur izin",
      targetTab: "jobs-board",
    },
    {
      id: "employees",
      title: "2. Tambah karyawan",
      status: usersStatus,
      detail:
        usersStatus === "SIAP"
          ? `${args.usersCount} pengguna tercatat.`
          : usersStatus === "BELUM_DIATUR"
            ? "Belum ada pengguna."
            : "Data pengguna belum terverifikasi.",
      actionLabel: "Tambah pengguna",
      targetTab: "users",
    },
    {
      id: "branches",
      title: "3. Tetapkan cabang",
      status: branchStatus,
      detail:
        branchStatus === "SIAP"
          ? "Semua pengguna aktif punya cabang."
          : branchStatus === "PERLU_DILENGKAPI"
            ? `${args.usersWithBranch}/${args.usersCount} pengguna punya cabang.`
            : "Cakupan cabang belum terverifikasi atau belum diatur.",
      actionLabel: "Lihat konfigurasi",
      targetTab: "users",
    },
    {
      id: "machines",
      title: "4. Konfigurasi mesin",
      status: fromLoad(args.load.machines, args.machinesCount),
      detail:
        args.machinesCount === 0
          ? "Belum ada mesin (boleh sengaja kosong)."
          : `${args.machinesCount} mesin tercatat.`,
      actionLabel: "Tambah mesin",
      targetTab: "machines",
    },
    {
      id: "shifts",
      title: "5. Atur shift",
      status: fromLoad(args.load.shifts, args.shiftsCount),
      detail:
        args.shiftsCount === 0
          ? "Belum ada shift (boleh sengaja kosong)."
          : `${args.shiftsCount} shift tercatat.`,
      actionLabel: "Atur shift",
      targetTab: "shifts",
    },
    {
      id: "readiness",
      title: "6. Tinjau kesiapan operasional",
      status: jobsStatus,
      detail: "Ringkasan kesiapan pekerjaan dan kapasitas cabang.",
      actionLabel: "Lihat konfigurasi",
      targetTab: "jobs-board",
    },
  ];
}

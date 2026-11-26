import { describe, expect, it } from "vitest";
import { buildGuidedSetupSteps } from "../guidedSetup";

describe("buildGuidedSetupSteps", () => {
  it("marks empty masters as BELUM_DIATUR not failure", () => {
    const steps = buildGuidedSetupSteps({
      rolesCount: 2,
      usersCount: 0,
      usersWithBranch: 0,
      machinesCount: 0,
      shiftsCount: 0,
      jobStatuses: [],
      load: {
        users: "DATA_EMPTY",
        machines: "DATA_EMPTY",
        shifts: "DATA_EMPTY",
        masterJobs: "DATA_EMPTY",
        branches: "SUCCESS",
        roles: "SUCCESS",
      },
    });
    expect(steps.find((s) => s.id === "employees")?.status).toBe("BELUM_DIATUR");
    expect(steps.find((s) => s.id === "machines")?.status).toBe("BELUM_DIATUR");
    expect(steps.find((s) => s.id === "ops-config")?.status).toBe("BELUM_DIATUR");
  });

  it("marks load failure as BELUM_TERVERIFIKASI", () => {
    const steps = buildGuidedSetupSteps({
      rolesCount: 1,
      usersCount: 3,
      usersWithBranch: 3,
      machinesCount: 1,
      shiftsCount: 1,
      jobStatuses: ["SIAP"],
      load: {
        users: "LOAD_FAILED",
        machines: "SUCCESS",
        shifts: "SUCCESS",
        masterJobs: "SUCCESS",
        branches: "SUCCESS",
        roles: "SUCCESS",
      },
    });
    expect(steps.find((s) => s.id === "employees")?.status).toBe("BELUM_TERVERIFIKASI");
  });
});

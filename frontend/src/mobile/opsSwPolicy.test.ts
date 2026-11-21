import { describe, expect, it } from "vitest";
import {
  OPS_CACHE_PREFIX,
  OPS_SHELL_CACHE,
  OPS_SW_SCOPE,
  OPS_SW_SCRIPT_URL,
  isEternalOpsCacheName,
  isOpsSwRegistrationValid,
  opsCachesToDelete,
  resolveOpsSwRegistration,
} from "./opsSwPolicy";

describe("opsSwPolicy registration", () => {
  it("plans script under /app/m/ so default max-scope allows /app/m/", () => {
    const plan = resolveOpsSwRegistration();
    expect(plan.scriptURL).toBe("/app/m/sw.js");
    expect(plan.scope).toBe("/app/m/");
    expect(plan.scriptURL.startsWith(plan.scope)).toBe(true);
    expect(isOpsSwRegistrationValid(plan.scriptURL, plan.scope)).toBe(true);
  });

  it("rejects legacy /ops/sw.js claiming /app/m/ scope", () => {
    expect(isOpsSwRegistrationValid("/ops/sw.js", "/app/m/")).toBe(false);
    expect(isOpsSwRegistrationValid(OPS_SW_SCRIPT_URL, "/ops/")).toBe(false);
    expect(isOpsSwRegistrationValid(OPS_SW_SCRIPT_URL, OPS_SW_SCOPE)).toBe(
      true,
    );
  });
});

describe("opsSwPolicy cache isolation", () => {
  it("only deletes stale ep-ops-* caches", () => {
    const keys = [
      OPS_SHELL_CACHE,
      "ep-ops-shell-v0",
      "elt-shell-v1",
      "workbox-precache-v2",
      "other-app-cache",
      `${OPS_CACHE_PREFIX}tmp`,
    ];
    expect(opsCachesToDelete(keys)).toEqual([
      "ep-ops-shell-v0",
      "ep-ops-tmp",
    ]);
  });

  it("never flags foreign caches as Eternal Ops", () => {
    expect(isEternalOpsCacheName("elt-shell-v1")).toBe(false);
    expect(isEternalOpsCacheName("workbox-precache-v2")).toBe(false);
    expect(isEternalOpsCacheName(OPS_SHELL_CACHE)).toBe(true);
  });
});

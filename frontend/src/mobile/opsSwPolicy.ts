/**
 * Kebijakan Service Worker Eternal Ops Mobile.
 * Scope SW harus berasal dari path di bawah max-scope default script
 * (direktori script). Script di /ops/sw.js tidak boleh claim /app/m/.
 */

export const OPS_SW_SCRIPT_URL = "/app/m/sw.js";
export const OPS_SW_SCOPE = "/app/m/";
export const OPS_CACHE_PREFIX = "ep-ops-";
export const OPS_SHELL_CACHE = "ep-ops-shell-v1";

export type OpsSwRegistrationPlan = {
  scriptURL: string;
  scope: string;
};

export function resolveOpsSwRegistration(): OpsSwRegistrationPlan {
  return { scriptURL: OPS_SW_SCRIPT_URL, scope: OPS_SW_SCOPE };
}

/**
 * Valid bila script berada di bawah scope yang diklaim
 * (default max scope = direktori script → /app/m/).
 */
export function isOpsSwRegistrationValid(
  scriptURL: string,
  scope: string,
): boolean {
  if (!scriptURL || !scope) return false;
  if (scriptURL !== OPS_SW_SCRIPT_URL) return false;
  if (scope !== OPS_SW_SCOPE && scope !== "/app/m") return false;
  // Script path must live under the claimed scope directory.
  return scriptURL.startsWith(scope);
}

export function isEternalOpsCacheName(name: string): boolean {
  return name.startsWith(OPS_CACHE_PREFIX);
}

/** Hanya hapus cache namespace Ops yang sudah usang — jangan sentuh cache lain. */
export function opsCachesToDelete(
  cacheKeys: string[],
  currentShell = OPS_SHELL_CACHE,
): string[] {
  return cacheKeys.filter(
    (key) => key !== currentShell && isEternalOpsCacheName(key),
  );
}

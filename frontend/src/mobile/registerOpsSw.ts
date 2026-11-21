/**
 * Register Eternal Ops SW.
 * Script /app/m/sw.js → scope /app/m/ (valid max-scope default).
 * Tidak menyentuh /t/ tracking SW.
 */

import {
  isOpsSwRegistrationValid,
  resolveOpsSwRegistration,
} from "./opsSwPolicy";

export async function registerOpsServiceWorker(): Promise<boolean> {
  if (typeof window === "undefined") return false;
  if (!("serviceWorker" in navigator)) return false;
  // Hanya di path PWA ops agar tidak mengintervensi desktop.
  if (!window.location.pathname.startsWith("/app/m")) return false;

  const plan = resolveOpsSwRegistration();
  if (!isOpsSwRegistrationValid(plan.scriptURL, plan.scope)) return false;

  try {
    const reg = await navigator.serviceWorker.register(plan.scriptURL, {
      scope: plan.scope,
    });
    // Fail closed bila browser menolak/menyempitkan scope di luar /app/m/.
    const activeScope = reg.scope || "";
    if (!activeScope.includes("/app/m")) return false;
    return true;
  } catch {
    return false;
  }
}

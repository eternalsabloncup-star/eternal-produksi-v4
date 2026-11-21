/** Register SW scoped ke /ops/ — tidak menyentuh /t/ tracking SW. */

export async function registerOpsServiceWorker(): Promise<boolean> {
  if (typeof window === "undefined") return false;
  if (!("serviceWorker" in navigator)) return false;
  // Hanya di path PWA ops agar tidak mengintervensi desktop.
  if (!window.location.pathname.startsWith("/app/m")) return false;
  try {
    await navigator.serviceWorker.register("/ops/sw.js", { scope: "/app/m/" });
    return true;
  } catch {
    return false;
  }
}

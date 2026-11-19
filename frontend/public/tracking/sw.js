/* Eternal Live Tracking — scoped service worker.
 * Scope: /t/ only. Does NOT intercept /app desktop routes.
 * Never caches financial documents or authenticated API payloads.
 */
const SHELL = "elt-shell-v1";
const SHELL_URLS = ["/tracking/offline.html", "/tracking/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL).then((cache) => cache.addAll(SHELL_URLS)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))),
    ).then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

  // Jangan cache API / dokumen — selalu network.
  if (url.pathname.startsWith("/api/")) return;

  // Hanya navigasi /t/* yang punya offline fallback aman.
  if (event.request.mode === "navigate" && url.pathname.startsWith("/t/")) {
    event.respondWith(
      fetch(event.request).catch(() => caches.match("/tracking/offline.html")),
    );
  }
});

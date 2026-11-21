/* Eternal Ops Mobile — scoped service worker.
 * Scope intent: /app/m/ (registered with that scope).
 * Never caches authenticated API payloads or financial documents.
 */
const SHELL = "ep-ops-shell-v1";
const SHELL_URLS = ["/ops/offline.html", "/ops/manifest.webmanifest"];

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
  if (url.pathname.startsWith("/api/")) return;

  if (
    event.request.mode === "navigate" &&
    url.pathname.startsWith("/app/m")
  ) {
    event.respondWith(
      fetch(event.request).catch(() => caches.match("/ops/offline.html")),
    );
  }
});

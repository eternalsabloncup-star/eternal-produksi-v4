/* Eternal Ops Mobile — service worker.
 * Script URL: /app/m/sw.js → default max scope /app/m/
 * Registered scope: /app/m/
 * Never caches authenticated API payloads or financial documents.
 * Cache cleanup is limited to ep-ops-* namespace only.
 */
const SHELL = "ep-ops-shell-v1";
const OPS_CACHE_PREFIX = "ep-ops-";
const SHELL_URLS = ["/ops/offline.html", "/ops/manifest.webmanifest"];

function isEternalOpsCacheName(name) {
  return typeof name === "string" && name.indexOf(OPS_CACHE_PREFIX) === 0;
}

function opsCachesToDelete(keys, currentShell) {
  return keys.filter(function (key) {
    return key !== currentShell && isEternalOpsCacheName(key);
  });
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL)
      .then((cache) => cache.addAll(SHELL_URLS))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          opsCachesToDelete(keys, SHELL).map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
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

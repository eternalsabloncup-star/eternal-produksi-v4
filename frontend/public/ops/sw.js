/* DEPRECATED — do not register this worker.
 * /ops/sw.js cannot claim scope /app/m/ (max scope is /ops/).
 * Active worker: /app/m/sw.js with scope /app/m/
 * This stub does not install, does not claim clients, and does not touch CacheStorage.
 */
self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  // Unregister this obsolete worker if somehow still registered.
  event.waitUntil(
    self.registration
      .unregister()
      .then(() => self.clients.claim())
      .catch(() => undefined),
  );
});

/* Lantern service worker.
 *
 * Caches only the public application shell (HTML, built assets, icons).
 * Never caches /api responses or document downloads: those are private,
 * served with Cache-Control: no-store, and always fetched from the network.
 * Navigations fall back to the cached shell only when offline.
 */
const VERSION = "lantern-shell-v3";
const SHELL = ["/", "/index.html", "/manifest.webmanifest", "/favicon.svg", "/icons/icon-192.png", "/icons/icon-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

function isPrivate(url) {
  return url.pathname.startsWith("/api/");
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || isPrivate(url)) return; // network only, never cached
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match("/index.html")));
    return;
  }
  if (url.pathname.startsWith("/assets/") || url.pathname.startsWith("/icons/") || SHELL.includes(url.pathname)) {
    event.respondWith(
      caches.match(req).then((hit) => {
        const fresh = fetch(req).then((res) => {
          if (res.ok) caches.open(VERSION).then((c) => c.put(req, res.clone()));
          return res;
        });
        return hit || fresh;
      }),
    );
  }
});

self.addEventListener("message", (event) => {
  if (event.data === "clear") caches.keys().then((keys) => keys.forEach((k) => caches.delete(k)));
});

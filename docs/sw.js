// Offline copy of the weekly site (UX review 2026-11-14c, product-10).
// Same-origin files are fetched from the network first, so a reader online always gets this week's
// data; the cached copy is only a fallback when offline. The versioned chart library is cache-first.
const CACHE = "radar-v1";
const SHELL = ["./", "index.html", "app.js", "style.css", "manifest.webmanifest",
  "data/summary.json", "data/universe.json", "data/market.json", "data/leading.json", "data/ai.json"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.hostname === "cdn.jsdelivr.net") {  // versioned URL: never changes, so cache-first is safe
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); return res; })));
    return;
  }
  if (url.origin !== location.origin) return;
  e.respondWith(fetch(req).then((res) => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
    return res;
  }).catch(() => caches.match(req, { ignoreSearch: true })));
});

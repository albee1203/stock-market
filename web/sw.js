/* Service worker：App 外殼離線可用；資料採「網路優先、失敗用快取」，離線時顯示上次資料。 */
const VERSION = "v1";
const SHELL = `shell-${VERSION}`;
const DATA = `data-${VERSION}`;
const SHELL_FILES = ["./", "index.html", "style.css", "app.js", "manifest.webmanifest", "icons/icon.svg", "icons/icon-192.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== SHELL && k !== DATA).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.includes("/data/")) {
    e.respondWith(fetch(e.request).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(DATA).then(c => c.put(e.request, copy)); }
      return res;
    }).catch(() => caches.match(e.request).then(r => r || Response.error())));
    return;
  }
  // 外殼：先用快取秒開，同時在背景更新
  e.respondWith(caches.match(e.request).then(cached => {
    const net = fetch(e.request).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(SHELL).then(c => c.put(e.request, copy)); }
      return res;
    }).catch(() => cached);
    return cached || net;
  }));
});

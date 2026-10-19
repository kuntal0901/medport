// MedPort phone app: keeps the app itself available offline. Health data never goes in this cache;
// it lives encrypted in IndexedDB and is only decrypted in memory after the PIN is entered.
const CACHE = "medport-shell-v1";
const SHELL = ["/m/", "/m/manifest.webmanifest", "/m/icon-192.png", "/m/icon-512.png", "/m/icon-180.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin || e.request.method !== "GET" || url.pathname.startsWith("/m/api/")) return;
  if (!SHELL.includes(url.pathname)) return;
  // network first so updates arrive at home; the cached copy when there is no Wi-Fi to the laptop
  // (away from home the laptop's address can hang rather than fail, so give up after 3 seconds)
  const timeout = new Promise((_, no) => setTimeout(() => no(new Error("timeout")), 3000));
  e.respondWith(
    Promise.race([fetch(e.request), timeout]).then((r) => {
      const copy = r.clone();
      caches.open(CACHE).then((c) => c.put(url.pathname, copy));
      return r;
    }).catch(() => caches.match(url.pathname))
  );
});

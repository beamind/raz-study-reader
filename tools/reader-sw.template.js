const CACHE = '__CACHE__';
const ASSETS = __ASSETS__;
const urls = new Set(ASSETS.map(path => new URL(path, self.registration.scope).href));
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)));
});
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key.startsWith('raz-reader-shell-') && key !== CACHE) await caches.delete(key);
    }
    await self.clients.claim();
  })());
});
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET' || !urls.has(event.request.url)) return;
  // Only public, same-origin shell URLs are allowed here. Ignore Vary so a
  // server's CORS Origin header cannot turn module/style requests into misses.
  event.respondWith((async () => (await (await caches.open(CACHE)).match(event.request, { ignoreVary: true })) || fetch(event.request))());
});

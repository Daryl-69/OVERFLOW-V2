/* Upstream service worker: offline app shell.
   Bump VERSION when shipping changes; the page shows an "update ready" toast and
   activates the new worker when the user taps Reload (SKIP_WAITING). */
const VERSION = 'upstream-v1';
const SHELL = [
  './', 'index.html', 'style.css', 'app.js', 'backend.js', 'pwa.js', 'config.js',
  'manifest.webmanifest', 'vendor/leaflet/leaflet.js', 'vendor/leaflet/leaflet.css',
  'icons/icon.svg', 'icons/icon-192.png', 'icons/icon-512.png', 'card.svg', 'data/scenario.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(VERSION).then((cache) =>
    // one by one: data/scenario.json only exists on static hosts, /api/scenario on the server
    Promise.all(SHELL.map((u) => cache.add(new Request(u, { cache: 'reload' })).catch(() => null)))));
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== VERSION) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
});

async function networkFirst(req, key) {
  const cache = await caches.open(VERSION);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(key || req, res.clone());
    return res;
  } catch (e) {
    const hit = await cache.match(key || req, { ignoreSearch: true });
    if (hit) return hit;
    throw e;
  }
}

async function staleWhileRevalidate(req) {
  const cache = await caches.open(VERSION);
  const hit = await cache.match(req, { ignoreSearch: true });
  const net = fetch(req).then((res) => {
    if (res.ok && res.type !== 'opaque') cache.put(req, res.clone());
    return res;
  }).catch(() => null);
  return hit || (await net) || Response.error();
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  const scope = new URL(self.registration.scope);
  if (url.origin === location.origin && url.pathname === '/api/scenario') {
    event.respondWith(networkFirst(req));
    return;
  }
  if (url.origin === location.origin && url.pathname.startsWith('/api/')) return;  // live data: never cached
  if (req.mode === 'navigate' && url.origin === location.origin && url.pathname.startsWith(scope.pathname)) {
    event.respondWith(networkFirst(req, new URL('index.html', scope).href)
      .catch(() => caches.match(new URL('./', scope).href)));
    return;
  }
  if (url.origin === location.origin && url.pathname.startsWith(scope.pathname)) {
    event.respondWith(staleWhileRevalidate(req));
    return;
  }
  if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    event.respondWith(staleWhileRevalidate(req));
  }
});

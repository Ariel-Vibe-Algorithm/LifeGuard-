// LifeGuard service worker (scope /app/).
// - Web Push: shows the notification the server sent (content is minimal by design: no location, no medical info).
// - Offline start: the app shell is cached. API calls (/v1/...) are NEVER cached or answered from the cache:
//   offline they fail, and the app tells the user plainly that nothing was sent.
// - AI hazard models (/app/models/, ~15MB): NOT part of the shell and never fetched on first load. The page downloads
//   them into MODEL_CACHE only after the user turns on "listening for danger" (hazard.js); here they are served
//   network-first with that cache as the offline fallback, and never copied into the shell cache.
// - iOS/iPadOS (home-screen web app, 16.4+): every push MUST show a notification, or Safari revokes the
//   subscription. The push handler therefore always calls showNotification, whatever the payload looks like.
const CACHE = 'lg-shell-v8';
const MODEL_CACHE = 'lg-hazard-models-v1';   // same name in hazard.js
const SHELL = ['/app/', '/app/index.html', '/app/app.css', '/app/app.js', '/app/api.js', '/app/icons.js',
  '/app/envmap.js', '/app/link.css', '/app/hazard.js', '/app/hazard_core.js',
  '/app/manifest.webmanifest', '/app/icon.svg', '/app/icon-192.png', '/app/icon-512.png',
  '/app/apple-touch-icon-180.png', '/app/apple-touch-icon-167.png', '/app/apple-touch-icon-152.png',
  '/app/apple-touch-icon-120.png'];
// a hanging network ("lie-fi") must not keep the app from opening: after this long, serve the cached shell
const NAV_TIMEOUT_MS = 4000;
const URGENT = new Set(['incident_new', 'escort_alarm']);

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key !== CACHE && key !== MODEL_CACHE) await caches.delete(key);
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || !url.pathname.startsWith('/app/')) return; // API & others: network only
  if (url.pathname.startsWith('/app/models/')) {
    event.respondWith(fetch(req).catch(async (e) => (await caches.match(req, { cacheName: MODEL_CACHE })) || Promise.reject(e)));
    return;
  }
  // network first (so updates arrive), cached shell when the network is unavailable or hangs
  const fromCache = async () => (await caches.match(req, { ignoreSearch: true }))
    || (req.mode === 'navigate' ? (await caches.match('/app/')) || caches.match('/app/index.html') : undefined);
  event.respondWith((async () => {
    const net = fetch(req).then((res) => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
      }
      return res;
    });
    net.catch(() => {}); // if the cache already answered, a late network failure is not an error
    let timer;
    const slow = new Promise((resolve) => { timer = setTimeout(() => resolve('slow'), NAV_TIMEOUT_MS); });
    try {
      const first = await Promise.race([net, slow]);
      if (first !== 'slow') return first;
      const cached = await fromCache();
      if (cached) return cached; // the network fetch keeps going and refreshes the cache for next time
      return await net;
    } catch (e) {
      const cached = await fromCache();
      if (cached) return cached;
      throw e;
    } finally {
      clearTimeout(timer);
    }
  })());
});

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { body: event.data ? event.data.text() : '' }; }
  const type = data.type || '';
  const title = data.title || 'LifeGuard';
  const options = {
    body: data.body || '',
    tag: data.tag || type || 'lifeguard',
    renotify: true,
    dir: 'rtl',
    lang: 'he',
    icon: '/app/icon-192.png',
    badge: '/app/icon-192.png',
    requireInteraction: URGENT.has(type),
    data: { url: data.url || '/app/', type },
  };
  event.waitUntil((async () => {
    try {
      await self.registration.showNotification(title, options);
    } catch (e) {
      // an option this browser rejects must never swallow an emergency alert (and on iOS a push without a
      // notification gets the subscription revoked): fall back to the bare minimum
      await self.registration.showNotification(title, { body: options.body, tag: options.tag, data: options.data });
    }
    // let an open app refresh at once (it still ACKs through its own notification stream)
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const w of wins) w.postMessage({ type: 'push', push_type: type, url: options.data.url });
  })());
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.url) || '/app/', self.location.origin).href;
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const w of wins) {
      if (new URL(w.url).pathname.startsWith('/app')) {
        await w.focus();
        w.postMessage({ type: 'navigate', url: target });
        return;
      }
    }
    await self.clients.openWindow(target);
  })());
});

/* Passport 助手 —— 离线缓存。
 * 缓存优先，后台更新。这样断网也能打开 App（蓝牙本来就不需要网络）。
 *
 * ⚠ 改完 index.html / app.js / style.css 之后一定要把下面的版本号加一，
 *   否则浏览器会一直用旧缓存，改了跟没改一样。 */
const CACHE = 'passport-pwa-v5';
const ASSETS = [
  './',
  './index.html',
  './style.css',
  './app.js',
  './manifest.webmanifest',
  './icon-192.png',
  './icon-512.png',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    caches.match(e.request).then(hit => {
      const net = fetch(e.request).then(res => {
        if (res && res.status === 200 && res.type === 'basic') {
          caches.open(CACHE).then(c => c.put(e.request, res.clone()));
        }
        return res;
      }).catch(() => hit);
      return hit || net;
    })
  );
});

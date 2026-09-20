/* Passport 助手 —— 离线缓存。
 *
 * ★ 策略（v9 起改动）：
 *   代码类文件（HTML / JS / CSS / webmanifest）走【网络优先】，
 *   图片资源走【缓存优先】。
 *
 *   以前整个站点都是"缓存优先 + 后台更新"，后果是：每次部署之后，
 *   用户第一次打开拿到的**必然是旧版**，要关掉再开一次才会更新 ——
 *   于是"改完跟没改一样"，白白怀疑代码有 bug。GitHub Pages 还会给
 *   所有文件发 Cache-Control: max-age=600，让这个坑更深。
 *
 *   现在网络优先请求带 cache:'no-cache'，强制跟服务器校验 ETag（通常
 *   只回 304，很便宜），拿到新版就立刻用新版。断网或 3 秒超时就回退到
 *   缓存 —— 蓝牙本来就不需要网络，离线仍然能打开。
 *
 * ⚠ 改完 index.html / app.js / style.css 之后仍然要把版本号加一。 */
const CACHE = 'passport-pwa-v10';
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
  e.waitUntil(
    caches.open(CACHE)
      // 逐个 add：某一个资源失败（比如暂时 404）不该让整次安装挂掉
      .then(c => Promise.all(ASSETS.map(u => c.add(u).catch(() => {}))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isDocOrCode(req, url) {
  if (req.mode === 'navigate') return true;
  if (/\/(?:index\.html)?$/.test(url.pathname)) return true;
  return /\.(?:js|css|webmanifest)$/.test(url.pathname);
}

function isAsset(url) {
  return /\.(?:png|jpe?g|svg|ico|webp)$/.test(url.pathname);
}

/* 网络优先：拿到就更新缓存并返回；失败/超时回退缓存 */
async function netFirst(req) {
  const cache = await caches.open(CACHE);
  try {
    const res = await Promise.race([
      fetch(req, { cache: 'no-cache' }),
      new Promise((_, rej) => setTimeout(() => rej(new Error('sw-timeout')), 3000)),
    ]);
    if (res && res.status === 200) cache.put(req, res.clone()).catch(() => {});
    return res;
  } catch (_) {
    const hit = await cache.match(req, { ignoreSearch: true });
    return hit || Response.error();
  }
}

/* 缓存优先：命中直接用，同时后台静默刷新 */
async function cacheFirst(req) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req, { ignoreSearch: true });
  const net = fetch(req).then(res => {
    if (res && res.status === 200) cache.put(req, res.clone()).catch(() => {});
    return res;
  }).catch(() => hit);
  return hit || net;
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (isDocOrCode(req, url)) e.respondWith(netFirst(req));
  else if (isAsset(url)) e.respondWith(cacheFirst(req));
});

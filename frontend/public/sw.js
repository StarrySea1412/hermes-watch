/* Hermes Watch service worker：仅启用 PWA 可安装性，不做资源缓存。
   面板是强实时的（SSE / 轮询 / 终端 WS），任何缓存策略都可能让用户看到过期巡检数据，
   因此 fetch 直接透传。离线场景交给浏览器自身的 HTTP 缓存。 */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(clients.claim()));
self.addEventListener('fetch', () => { /* passthrough */ });

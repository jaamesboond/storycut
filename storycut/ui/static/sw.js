/* storycut service worker: cache the app shell so it opens instantly (and offline).
   API calls always go to the network; results are never cached. */
const CACHE = "storycut-shell-v2";
const SHELL = ["/", "/static/app.css", "/static/app.js", "/static/logo.svg", "/static/icon-192.png",
               "/static/icon-512.png", "/static/favicon-64.png", "/manifest.webmanifest"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL))); self.skipWaiting(); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  e.respondWith(fetch(e.request).then(r => {
    const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); return r;
  }).catch(() => caches.match(e.request).then(r => r || caches.match("/"))));
});

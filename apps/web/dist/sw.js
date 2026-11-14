// Archive X service worker: keeps the app's own files so it opens fast and can be installed.
// It never touches API calls, streams or sockets, and never caches pages, so sign-in state and data are always live.
const CACHE = 'archive-x-static-v1'

self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key)
    await self.clients.claim()
  })())
})
self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.method !== 'GET') return
  const url = new URL(req.url)
  if (url.origin !== self.location.origin) return
  // Only the built, content-hashed files and the app icons: they never change under the same name.
  if (!url.pathname.startsWith('/assets/') && !url.pathname.startsWith('/icons/')) return
  event.respondWith((async () => {
    const cache = await caches.open(CACHE)
    const hit = await cache.match(req)
    if (hit) return hit
    const res = await fetch(req)
    if (res.ok) cache.put(req, res.clone())
    return res
  })())
})

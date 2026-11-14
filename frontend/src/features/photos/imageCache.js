/** Decoded-image cache for the viewer: a photo that was fetched ahead is already in memory, so showing it costs nothing. */
const MAX = 24;
const cache = new Map();
export function loadImage(url) {
    const hit = cache.get(url);
    if (hit)
        return hit;
    const p = new Promise((resolve, reject) => {
        const img = new Image();
        img.decoding = 'async';
        img.onload = () => { void (img.decode ? img.decode() : Promise.resolve()).catch(() => undefined).then(() => resolve(img)); };
        img.onerror = () => reject(new Error(`Could not load ${url}`));
        img.src = url;
    });
    cache.set(url, p);
    p.catch(() => cache.delete(url));
    while (cache.size > MAX)
        cache.delete(cache.keys().next().value);
    return p;
}
export const isCached = (url) => cache.has(url);
export const clearImageCache = () => cache.clear();
/** The first of these to load; fails only if every one fails. */
export const firstLoaded = (urls) => Promise.any(urls.map((url) => loadImage(url).then((img) => ({ url, img }))));

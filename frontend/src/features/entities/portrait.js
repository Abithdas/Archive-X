export const DEFAULT_CROP = Object.freeze({ x: 0, y: 0, zoom: 1 });
export const ZOOM_MIN = 1;
export const ZOOM_MAX = 2.5;
export const STORAGE_KEY = 'archive-x.performer-portrait-framing.v1';
/** A first drag on an unzoomed photo zooms in a little so there is something to move. */
const FIRST_DRAG_ZOOM = 1.15;
const clamp = (v, min, max) => Math.min(max, Math.max(min, Number.isFinite(Number(v)) ? Number(v) : min));
/** How far (in percent of the frame) the photo may be moved at this zoom. */
export const maxPan = (zoom) => Math.max(0, (zoom - 1) * 47);
export function normalizeCrop(v) {
    const zoom = clamp(v?.zoom ?? DEFAULT_CROP.zoom, ZOOM_MIN, ZOOM_MAX);
    const lim = maxPan(zoom);
    return { x: clamp(v?.x ?? 0, -lim, lim) + 0, y: clamp(v?.y ?? 0, -lim, lim) + 0, zoom };
}
function readStore() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        const v = raw ? JSON.parse(raw) : {};
        return v && typeof v === 'object' && !Array.isArray(v) ? v : {};
    }
    catch {
        return {};
    }
}
export const readCrop = (id) => normalizeCrop(readStore()[String(id)] ?? DEFAULT_CROP);
/** Returns false when the browser refuses to store it (private mode, quota). */
export function writeCrop(id, crop) {
    try {
        const store = readStore();
        store[String(id)] = normalizeCrop(crop);
        localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
        return true;
    }
    catch {
        return false;
    }
}
export const isDefaultCrop = (c) => c.x === 0 && c.y === 0 && c.zoom === 1;
export function startDrag(crop) {
    return crop.zoom <= 1.001 ? normalizeCrop({ ...crop, zoom: FIRST_DRAG_ZOOM }) : crop;
}
/** Crop after dragging the photo `dx`,`dy` pixels inside a frame of `w`×`h` pixels, starting from `from`. */
export function dragCrop(from, dx, dy, w, h) {
    if (!w || !h)
        return from;
    const lim = maxPan(from.zoom);
    return { ...from, x: clamp(from.x + (dx / w) * 100, -lim, lim), y: clamp(from.y + (dy / h) * 100, -lim, lim) };
}
export const withZoom = (crop, zoom) => normalizeCrop({ ...crop, zoom });
/** CSS variables the portrait image reads (see PortraitImage). */
export function cropVars(crop) {
    const c = normalizeCrop(crop);
    return { '--portrait-pan-x': `${c.x}%`, '--portrait-pan-y': `${c.y}%`, '--portrait-zoom': String(c.zoom) };
}
/** Inline style for the portrait <img>: the established slightly-high composition plus the saved pan and zoom. */
export function imageStyle(crop) {
    const c = normalizeCrop(crop);
    return { objectPosition: 'center 25%', transform: `translate(${c.x}%, ${c.y}%) scale(${c.zoom})`, transformOrigin: 'center center' };
}

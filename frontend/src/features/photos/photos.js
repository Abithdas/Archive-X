/** Pure logic for the Photos page and viewer (mirrors the classic photo library). */
export const FAVORITES_KEY = 'archive-x-photo-favorites-v1';
export const PER_PAGE = 48;
export const SORTS = [
    { value: 'date_desc', label: 'Newest first' }, { value: 'date_asc', label: 'Oldest first' },
    { value: 'name_asc', label: 'Name A–Z' }, { value: 'size_desc', label: 'Largest first' },
];
export const sortLabel = (v) => SORTS.find((s) => s.value === v)?.label ?? SORTS[0].label;
export const NO_CHANNELS_SAVED = 'No saved Telegram channels found. Open Videos and refresh the Telegram channel list first.';
export const NO_SCANNED = 'No channels have a completed Image scan yet. Open Transfers → Metadata & Assets Manager → Processing Stages → Advanced options, enable Image scan, select channels, and run it.';
export const SELECT_PROMPT = 'Select channel cards to browse indexed photos. To index channel history, use Metadata & Assets Manager → Processing Stages → Advanced options → Image scan.';
export const EMPTY_INDEX = 'No photos indexed in these channels yet. Run Image scan in Metadata & Assets Manager → Processing Stages → Advanced options.';
export const LOAD_FAILED = 'Could not load photo records.';
export const channelTitle = (c) => {
    const t = String(c.title ?? '').trim();
    return t && !/^unknown channel$/i.test(t) && !/^channel\s+-?\d+$/i.test(t) && !/^-?\d+$/.test(t) ? t : 'Unknown channel';
};
export const initials = (title) => title.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => Array.from(p)[0] ?? '').join('').toUpperCase() || 'TG';
/** Only channels with a completed image scan are browsable, sorted by name. Rows from the photo index are merged over the saved list. */
export function scannedChannels(saved, photo) {
    const byId = new Map(photo.map((c) => [String(c.telegram_id), c]));
    const seen = new Set();
    const merged = saved.map((c) => {
        const id = String(c.telegram_id);
        seen.add(id);
        const p = byId.get(id);
        return { ...(p ?? {}), ...c, photo_count: Number(p?.photo_count ?? c.photo_count ?? 0), photo_scan_status: String(p?.photo_scan_status || c.photo_scan_status || 'NEVER').toUpperCase(), photo_last_scan_at: p?.photo_last_scan_at ?? null };
    });
    for (const c of photo) {
        const id = String(c.telegram_id);
        if (id && !seen.has(id)) {
            merged.push(c);
            seen.add(id);
        }
    }
    return merged.filter((c) => String(c.photo_scan_status || 'NEVER').toUpperCase() === 'COMPLETE').sort((a, b) => channelTitle(a).localeCompare(channelTitle(b)));
}
export const channelAvatar = (c) => {
    const id = encodeURIComponent(String(c.telegram_id));
    const v = encodeURIComponent(String(c.updated_at || '0'));
    return String(c.image || '').startsWith('manual-upload:') ? `/api/channels/${id}/image?v=${v}&w=160` : `/api/channels/${id}/telegram-photo?v=${v}`;
};
export const scanTitle = (c) => `${c.photo_last_scan_at ? `Last completed image scan ${new Date(c.photo_last_scan_at).toLocaleString()}` : 'Image scan complete'} · ${Number(c.photo_count || 0).toLocaleString()} photos`;
export const channelSummary = (n) => `${n.toLocaleString()} scanned channels`;
export const selectedLabel = (n) => `${n} channel${n === 1 ? '' : 's'} selected`;
export function toggleId(selected, id) { return selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]; }
export const allSelected = (channels, selected) => channels.length > 0 && channels.every((c) => selected.includes(String(c.telegram_id)));
export const toggleAll = (channels, selected) => (allSelected(channels, selected) ? [] : channels.map((c) => String(c.telegram_id)));
/** Selection limited to channels that still exist (a removed channel can't stay selected). */
export const pruneSelection = (selected, channels) => selected.filter((id) => channels.some((c) => String(c.telegram_id) === id));
export function photoParams(q) {
    const p = new URLSearchParams({ page: String(q.page), per: String(q.per ?? PER_PAGE), channels: q.channels.join(','), q: q.q, sort: q.sort });
    const f = q.face;
    if (f?.performerId)
        p.set('face_performer_id', String(f.performerId));
    else if (f?.groupIds.length)
        p.set('face_group_ids', f.groupIds.join(','));
    else if (f?.groupId && f.groupId > 0)
        p.set('face_group_id', String(f.groupId));
    if (f?.scope)
        p.set('face_scope', f.scope);
    if (q.contentTags?.length)
        p.set('content_tags', q.contentTags.join(','));
    if (q.favorites) {
        p.set('favorites', '1');
        p.set('favorite_ids', [...q.favoriteIds].slice(0, 900).join(','));
    }
    return p;
}
export const photosUrl = (q) => `/api/photos?${photoParams(q)}`;
export function readFavorites(storage) {
    try {
        const v = JSON.parse(storage?.getItem(FAVORITES_KEY) || '[]');
        return new Set(Array.isArray(v) ? v.map(String) : []);
    }
    catch {
        return new Set();
    }
}
export function writeFavorites(storage, ids) { try {
    storage?.setItem(FAVORITES_KEY, JSON.stringify([...ids]));
}
catch { /* storage may be disabled */ } }
export const favKey = (p) => String(p.id || '');
export function toggleFavorite(ids, p) { const k = favKey(p); const n = new Set(ids); if (!k)
    return n; if (n.has(k))
    n.delete(k);
else
    n.add(k); return n; }
export const emptyText = (search, favorites) => search ? { title: 'No photos match this search.', body: 'Try another filename, caption, or channel name.' }
    : favorites ? { title: 'No favourite photos here yet.', body: 'Star a photo in the viewer to find it here.' }
        : { title: 'No photos indexed in selected channels yet.', body: 'Use Metadata & Assets Manager → Processing Stages → Advanced options → Image scan.' };
export const summaryText = (total) => `${total.toLocaleString()} image${total === 1 ? '' : 's'}`;
export const photoName = (p) => String(p.filename || `Photo ${p.message_id}`);
export const ratio = (p) => (p.width > 0 && p.height > 0 ? `${p.width} / ${p.height}` : '4 / 3');
export function formatBytes(v) {
    const b = Number(v || 0);
    if (!(b > 0))
        return '';
    if (b < 1024)
        return `${b} B`;
    if (b < 1024 * 1024)
        return `${(b / 1024).toFixed(0)} KB`;
    return `${(b / (1024 * 1024)).toFixed(1)} MB`;
}
export function formatDate(v) {
    if (!v)
        return '';
    const s = String(v);
    const d = /^\d{10,13}$/.test(s) ? new Date(Number(s) < 1e11 ? Number(s) * 1000 : Number(s)) : new Date(s);
    return Number.isFinite(d.getTime()) ? new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: '2-digit' }).format(d) : s.slice(0, 24);
}
export function hud(p) {
    const ok = p.width > 0 && p.height > 0;
    const d = formatDate(p.message_date);
    return { title: photoName(p), dims: ok ? `${p.width} × ${p.height}` : '', mp: ok ? `${((p.width * p.height) / 1e6).toFixed(1)} MP` : '', size: formatBytes(p.size), date: d ? `Message · ${d}` : '', channel: String(p.channel_title || '') };
}
export const counterText = (index, total) => `${Math.max(0, index) + 1} / ${Math.max(1, total)}`;
export const ZOOM_STEP = 1.25;
export const MAX_ZOOM = 8;
/** How far below the fitted size the photo may be shrunk (a tenth of it). */
export const MIN_REL = 0.1;
/** Fit scale for an image shown inside a stage, accounting for 90° rotations. Never upscales past 1. */
export function fitScale(img, stage, rotation) {
    const turned = Math.abs(rotation) % 180 === 90;
    const w = turned ? img.h : img.w, h = turned ? img.w : img.h;
    if (!(w > 0 && h > 0 && stage.w > 0 && stage.h > 0))
        return 1;
    return Math.min(1, stage.w / w, stage.h / h);
}
export const zoomBy = (scale, direction, fit) => Math.min(MAX_ZOOM, Math.max(fit * MIN_REL, scale * (direction < 0 ? 1 / ZOOM_STEP : ZOOM_STEP)));
export const zoomPercent = (scale, fit) => `${Math.round((fit > 0 ? scale / fit : 1) * 100)}%`;
export const rotate = (current, delta) => (((current + delta) % 360) + 360) % 360;
/** Keep a panned image inside the stage. A photo larger than the stage can move until its far edge reaches the stage edge; a smaller one can be placed anywhere in the stage. */
export function clampPan(x, y, img, stage, scale, rotation) {
    const turned = Math.abs(rotation) % 180 === 90;
    const w = (turned ? img.h : img.w) * scale, h = (turned ? img.w : img.h) * scale;
    const mx = Math.abs(w - stage.w) / 2, my = Math.abs(h - stage.h) / 2;
    return { x: Math.min(mx, Math.max(-mx, x)), y: Math.min(my, Math.max(-my, y)) };
}
/** Next viewer index; manual steps stop at the ends, slideshow playback wraps. */
export function stepIndex(i, delta, total, wrap = false) {
    if (total <= 0)
        return null;
    const t = i + delta;
    if (t >= 0 && t < total)
        return t;
    return wrap ? (t < 0 ? total - 1 : 0) : null;
}
export const STRIP_WINDOW = 24;
/** The filmstrip shows a window of thumbnails centred on the current photo. */
export function stripRange(index, total, size = STRIP_WINDOW) {
    const start = Math.max(0, Math.min(index - Math.floor(size / 2), total - size));
    return [start, Math.min(total, start + size)];
}
export const SLIDE_MS = 3000;
export const SWIPE_PX = 60;
export const deleteCopy = (remote, name, channel) => remote
    ? { title: 'Delete photo and Telegram message?', ok: 'Delete from both', text: `Permanently delete "${name}" from Archive X and its Telegram message in "${channel}"?\n\nThis cannot be undone. Archive X will keep the cached image if Telegram deletion fails.` }
    : { title: 'Remove photo from Archive X?', ok: 'Remove', text: `Remove "${name}" from the Archive X Photos library and delete its local cached copies?\n\nThe Telegram message will remain and may be indexed again if its channel is scanned in the future.` };
export const deleteBody = (remote) => ({ scope: remote ? 'archive_and_telegram' : 'archive_only' });
export const deletePath = (p) => `/api/photos/${encodeURIComponent(p.channel_id)}/${p.message_id}/delete`;
export const deleteStatus = (remote, name) => (remote ? `Deleting ${name} from Telegram and Archive X…` : `Removing ${name} from Archive X…`);
export const deleteDone = (remote, r) => r.message || (remote ? 'Photo deleted from Archive X and Telegram.' : 'Photo removed from Archive X.');
/** Pictures worth fetching ahead while one photo is open: the next two and the previous one, the full image first and the preview as a quick fallback. */
export function neighborUrls(items, index) {
    return [index + 1, index + 2, index - 1].flatMap((i) => [items[i]?.image_url, items[i]?.preview_url]).filter((u) => Boolean(u));
}
/** Mouse wheel zoom: a smooth exponential factor (same feel as the classic viewer), whatever unit the wheel reports. */
export function wheelFactor(deltaY, deltaMode = 0, stageHeight = 800) {
    const px = deltaMode === 1 ? deltaY * 16 : deltaMode === 2 ? deltaY * stageHeight : deltaY;
    return Math.exp(-Math.max(-2400, Math.min(2400, px)) * 0.0018);
}
/** New pan after changing the scale from `from` to `to` while keeping the point under the cursor fixed. `cursor` is measured from the stage centre. */
export function anchorPan(pan, from, to, cursor) {
    const k = from > 0 ? to / from : 1;
    return { x: cursor.x - (cursor.x - pan.x) * k, y: cursor.y - (cursor.y - pan.y) * k };
}
/** Wheel over the toolbar or filmstrip turns pages: one photo per notch, up to three for a big spin. 0 when there is no movement. */
export function wheelSteps(deltaX, deltaY) {
    const raw = Math.abs(deltaX) > Math.abs(deltaY) ? deltaX : deltaY;
    if (!raw)
        return 0;
    return (raw > 0 ? 1 : -1) * Math.max(1, Math.min(3, Math.round(Math.abs(raw) / 120) || 1));
}
export const clampScale = (scale, fit) => Math.min(MAX_ZOOM, Math.max(fit * MIN_REL, scale));
/** Space kept clear around a fitted photo: top and bottom, and the sides. */
export const PAD_Y = 20;
export const PAD_X = 16;
export const paddedBox = (box) => ({ w: Math.max(1, box.w - 2 * PAD_X), h: Math.max(1, box.h - 2 * PAD_Y) });
/** Two-finger pinch: the scale follows how far the fingers moved apart or together. */
export const pinchScale = (startScale, startDistance, distance) => (startDistance > 0 ? startScale * (distance / startDistance) : startScale);

export const DEFAULT_STYLE = { size: 1, bg: 'none', x: 0.5, y: 0.88 };
export const SIZE_MIN = 0.6;
export const SIZE_MAX = 2.4;
export const SIZE_STEP = 0.15;
const KEY = 'archive-x:caption-style';
const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));
export const clampSize = (n) => Math.round(clamp(n, SIZE_MIN, SIZE_MAX) * 100) / 100;
/** Keep the text centre inside the picture, with a margin so it is always reachable. */
export const clampPos = (n) => clamp(n, 0.06, 0.94);
export function normalizeStyle(v) {
    const bg = v?.bg === 'soft' || v?.bg === 'solid' || v?.bg === 'none' ? v.bg : DEFAULT_STYLE.bg;
    return {
        size: Number.isFinite(Number(v?.size)) ? clampSize(Number(v.size)) : DEFAULT_STYLE.size,
        bg,
        x: Number.isFinite(Number(v?.x)) ? clampPos(Number(v.x)) : DEFAULT_STYLE.x,
        y: Number.isFinite(Number(v?.y)) ? clampPos(Number(v.y)) : DEFAULT_STYLE.y,
    };
}
export function loadStyle() { try {
    return normalizeStyle(JSON.parse(localStorage.getItem(KEY) ?? 'null'));
}
catch {
    return DEFAULT_STYLE;
} }
export function saveStyle(s) { try {
    localStorage.setItem(KEY, JSON.stringify(s));
}
catch { /* private mode */ } }
export const sizeLabel = (size) => `${Math.round(size * 100)}%`;

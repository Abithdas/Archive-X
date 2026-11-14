/** The 36-frame snapshot grid shown before playback. Layout and cropping ported from the classic canvas module. */
export const FRAME_COUNT = 36;
export const SPRITE_COLS = 6; // the prebuilt sprite is 6 × 6 frames
/** Only a complete, validated set of 36 frames is shown. */
export const gridReady = (m) => Boolean(m && m.frames_ready === true && Number(m.frame_count || 0) >= FRAME_COUNT);
/** The server's times if it sent all 36 valid ones, else 36 points spread over the video (2%–98%). */
export function gridTimes(m, duration) {
    const t = Array.isArray(m?.frame_times) && m.frame_times.length === FRAME_COUNT ? m.frame_times.map(Number) : [];
    if (t.length === FRAME_COUNT && t.every((x) => Number.isFinite(x) && x >= 0))
        return t;
    return Array.from({ length: FRAME_COUNT }, (_, i) => (duration > 0 ? duration * (0.02 + (0.96 * i) / (FRAME_COUNT - 1)) : i));
}
/** Chooses the rows × columns (a factor pair of 36) whose tiles best match the frame's shape, avoiding tiny tiles. */
export function layoutFor(width, height, frameAspect) {
    const W = Math.max(1, Number(width) || 1), H = Math.max(1, Number(height) || 1);
    const aspect = Math.max(0.25, Math.min(4, Number(frameAspect) || 16 / 9));
    let best = null;
    for (const rows of [1, 2, 3, 4, 6, 9, 12, 18, 36]) {
        const cols = FRAME_COUNT / rows, tileW = W / cols, tileH = H / rows;
        const crop = Math.abs(Math.log(Math.max(0.001, tileW / Math.max(1, tileH)) / aspect));
        const size = (tileW < 42 ? ((42 - tileW) / 42) * 0.035 : 0) + (tileH < 34 ? ((34 - tileH) / 34) * 0.035 : 0);
        const score = crop + size;
        if (!best || score < best.score)
            best = { rows, cols, score };
    }
    return best ? { rows: best.rows, cols: best.cols } : { rows: 6, cols: 6 };
}
/** CSS for one tile showing frame `index` of the sprite, scaled to cover the tile and centred (what the classic canvas drew). */
export function spriteTile(index, tileW, tileH, spriteW, spriteH) {
    const col = index % SPRITE_COLS, row = Math.floor(index / SPRITE_COLS);
    const cellW = spriteW / SPRITE_COLS, cellH = spriteH / SPRITE_COLS;
    if (!(cellW > 0 && cellH > 0 && tileW > 0 && tileH > 0))
        return { backgroundSize: 'auto', backgroundPosition: '0 0' };
    const scale = Math.max(tileW / cellW, tileH / cellH);
    const x = -(col * cellW * scale) - (cellW * scale - tileW) / 2;
    const y = -(row * cellH * scale) - (cellH * scale - tileH) / 2;
    return { backgroundSize: `${spriteW * scale}px ${spriteH * scale}px`, backgroundPosition: `${x}px ${y}px` };
}
/** The label is only drawn when the tile is large enough for it (as in the classic grid). */
export const showsLabel = (tileW, tileH, label) => tileW > label.length * 6.5 + 8 + 8 && tileH > 24;
export const tileLabel = (seconds) => `Play from ${seconds >= 3600 ? `${Math.floor(seconds / 3600)}:${String(Math.floor((seconds % 3600) / 60)).padStart(2, '0')}:${String(Math.floor(seconds % 60)).padStart(2, '0')}` : `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`}`;

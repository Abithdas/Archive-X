/** Stable masonry: each photo goes into the currently shortest column, decided once. Adding photos at the end never moves the ones already shown
 *  (CSS multi-column layout re-balances every card whenever more are loaded, so the gallery jumps while you scroll). */
export function assignColumns(aspects, columns) {
    const n = Math.max(1, Math.floor(columns) || 1);
    const cols = Array.from({ length: n }, () => []);
    const heights = new Array(n).fill(0);
    aspects.forEach((a, i) => {
        let best = 0;
        for (let c = 1; c < n; c++)
            if (heights[c] < heights[best] - 1e-9)
                best = c;
        cols[best].push(i);
        // a card one column wide and `1/aspect` tall; an unknown shape counts as 4:3
        heights[best] += 1 / (a > 0 && Number.isFinite(a) ? a : 4 / 3);
    });
    return cols;
}
export const aspectOf = (p) => (Number(p.width) > 0 && Number(p.height) > 0 ? Number(p.width) / Number(p.height) : 4 / 3);
/** Same breakpoints as the old layout: 2 columns, 3 from 640 px, 4 from 1024 px, 6 from 1280 px. */
export const columnsFor = (width) => (width >= 1280 ? 6 : width >= 1024 ? 4 : width >= 640 ? 3 : 2);

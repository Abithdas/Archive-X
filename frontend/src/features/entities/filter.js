/** Providers and older data say 'Female', 'transgender_female', 'Trans woman'...; the app groups them as female, male or trans. */
export const normalizeGender = (g) => {
    const t = String(g ?? '').trim().toLowerCase();
    return t.includes('trans') ? 'trans' : t === 'f' || t === 'woman' ? 'female' : t === 'm' || t === 'man' ? 'male' : t === 'female' || t === 'male' ? t : '';
};
/** Search by name (case and accent insensitive), optional gender filter, most videos first, then A-Z. */
export function filterEntities(items, query, gender = 'all') {
    const fold = (s) => s.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();
    const q = fold(query.trim());
    return items
        .filter((e) => (!q || fold(e.name).includes(q)) && (gender === 'all' || normalizeGender(e.gender) === gender))
        .sort((a, b) => b.video_count - a.video_count || a.name.localeCompare(b.name));
}
export const pageCount = (total, per) => Math.max(1, Math.ceil(total / per));

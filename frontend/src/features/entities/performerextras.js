/** Pure logic for the performer page's Details card, Studios and Similar Performers tabs (mirrors the classic page). */
import { careerRows, metaRecords, metaText } from './studioextras';
const COMPLETE = /^(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4}|[A-Za-z]{3,}\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]{3,}\s+\d{4})/;
/** The birth date as the provider wrote it, with the age added only when the date is complete and plausible. */
export function birthWithAge(value, now = new Date()) {
    const raw = metaText(value);
    if (!raw || !COMPLETE.test(raw))
        return raw;
    let born;
    const iso = /^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/.exec(raw);
    if (iso) {
        const y = Number(iso[1]), m = Number(iso[2]), d = Number(iso[3]);
        born = new Date(y, m - 1, d);
        if (born.getFullYear() !== y || born.getMonth() !== m - 1 || born.getDate() !== d)
            return raw;
    }
    else
        born = new Date(raw);
    if (!Number.isFinite(born.getTime()))
        return raw;
    let age = now.getFullYear() - born.getFullYear();
    if (now.getMonth() < born.getMonth() || (now.getMonth() === born.getMonth() && now.getDate() < born.getDate()))
        age--;
    return age < 0 || age > 125 ? raw : `${raw} (${age})`;
}
/** "LARGE NATURAL" → "Large Natural"; mixed-case text is left alone. */
export function prettyEnum(value) {
    const raw = metaText(value);
    return /^[A-Z][A-Z _-]+$/.test(raw) ? raw.toLowerCase().replace(/(^|[ _-])([a-z])/g, (_, sep, ch) => sep + ch.toUpperCase()) : raw;
}
export function detailRows(_p, meta, videoCount, now) {
    const recs = metaRecords(meta);
    const first = (keys) => {
        for (const r of recs)
            for (const o of [r.details, r.normalized])
                for (const k of keys) {
                    const v = metaText(o?.[k]);
                    if (v)
                        return v;
                }
        return '';
    };
    const rows = [
        ['Country', first(['country', 'nationality'])], ['Birth date', birthWithAge(first(['birth_date', 'date_of_birth', 'born']), now)], ['Ethnicity', first(['ethnicity'])],
        ['Height', first(['height', 'height_cm'])], ['Eye color', first(['eye_color', 'eyes'])], ['Hair color', first(['hair_color', 'hair'])],
        ['Breast type', prettyEnum(first(['breast_type', 'breastType']))], ['Cup size', first(['cup_size', 'cupSize'])], ['Career start', first(['career_start_year', 'career_start', 'career_start_date'])],
    ];
    return [...rows.filter(([, v]) => v.trim()), ['Videos in Archive X', videoCount.toLocaleString()], ...careerRows(meta)];
}
export const metadataLinked = (p, meta) => (Array.isArray(meta?.providers) && meta.providers.length > 0) || Boolean(p.provider_ids && p.provider_ids !== '{}' && !(typeof p.provider_ids === 'object' && !Object.keys(p.provider_ids).length));
export const isUnknownLocal = (p) => p.name.trim().toLowerCase() === 'unknown performer';
/** Studios that appear on this performer's videos, joined to the catalogue, most videos first. */
export function studiosFor(videos, all) {
    const counts = new Map();
    for (const v of videos)
        for (const s of Array.isArray(v.studios) ? v.studios : []) {
            const k = String(s ?? '').trim();
            if (k)
                counts.set(k, (counts.get(k) ?? 0) + 1);
        }
    return all.filter((x) => counts.has(String(x.name || ''))).map((x) => ({ ...x, count: counts.get(String(x.name)) })).sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}
export const studioCount = (videos) => new Set(videos.flatMap((v) => (Array.isArray(v.studios) ? v.studios : [])).map((x) => String(x ?? '').trim()).filter(Boolean)).size;
export const STUDIOS_EMPTY = 'No linked studios were found for this performer.';
export const similarEmpty = (reason) => (reason === 'gender_required' ? 'Similar performers require a known gender on the current profile.' : 'No similar performers with matching personal and physical details were found.');
export const PTABS = [{ value: 'videos', label: 'Videos' }, { value: 'studios', label: 'Studios' }, { value: 'similar', label: 'Similar Performers' }, { value: 'suggestions', label: 'Suggestions' }];

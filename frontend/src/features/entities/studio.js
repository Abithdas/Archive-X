/** Studio page rules: counts, provider pills, linked performers and the favourite toggle. Ported from the classic studio page. */
import { nameKey } from '@/features/watch/quickedit';
export const plural = (n, one, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;
/** Every performer who appears in the studio's videos, once each (accents and spacing ignored), most videos first. */
export function linkedPerformers(videos) {
    const map = new Map();
    for (const v of videos) {
        const seenInVideo = new Set();
        for (const raw of v.performers ?? []) {
            const name = String(raw ?? '').trim(), key = nameKey(name);
            if (!name || !key || seenInVideo.has(key))
                continue;
            seenInVideo.add(key);
            const cur = map.get(key);
            if (cur)
                cur.count += 1;
            else
                map.set(key, { name, count: 1 });
        }
    }
    return [...map.values()].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name));
}
/** provider_ids may arrive as JSON text or an object; empty values do not count. */
export function providerNames(ids) {
    let parsed = ids;
    if (typeof ids === 'string') {
        try {
            parsed = JSON.parse(ids || '{}');
        }
        catch {
            return [];
        }
    }
    if (!parsed || typeof parsed !== 'object')
        return [];
    return Object.entries(parsed).filter(([, v]) => v !== null && v !== undefined && String(v).trim()).map(([k]) => k);
}
export function providerPills(ids) {
    return providerNames(ids).map((name) => {
        const low = name.toLowerCase();
        const label = /stash/.test(low) ? 'StashDB' : /tpdb|porn.?db|theporn/.test(low) ? 'ThePornDB' : name;
        return { label, tone: label === 'StashDB' ? 'violet' : label === 'ThePornDB' ? 'accent' : 'plain' };
    });
}
export const metadataStatus = (site) => (providerNames(site.provider_ids).length ? 'Metadata linked' : 'Local profile');
export const BIO_LIMIT = 280;
export const bioText = (site) => String(site.bio ?? '').trim();
export const bioShown = (bio, open) => (bio.length > BIO_LIMIT && !open ? `${bio.slice(0, BIO_LIMIT).trimEnd()}…` : bio);
export const siteLink = (site) => { const u = String(site.site_url || site.url || '').trim(); return /^https?:\/\//i.test(u) ? u : ''; };
export const initial = (name) => name.trim().charAt(0).toUpperCase() || '?';
export const imagePath = (id, image) => `/api/studios/${id}/image?v=${encodeURIComponent(String(image ?? ''))}`;
export const TABS = [{ value: 'videos', label: 'Videos' }, { value: 'performers', label: 'Performers' }, { value: 'similar', label: 'Similar Studios' }, { value: 'suggestions', label: 'Suggestions' }];
export const favLabel = (on) => (on ? 'Remove from favourites' : 'Add to favourites');
export const favText = (on) => (on ? 'Favourite' : 'Add to favourites');
export const NOT_FOUND = 'Studio not found.';

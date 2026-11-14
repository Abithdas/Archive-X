/** Pure logic for the studio page's Details card, social links, Similar Studios and Suggestions (mirrors the classic page). */
import { plural } from './studio';
export const metaText = (v) => {
    if (v === null || v === undefined || v === '')
        return '';
    if (Array.isArray(v))
        return v.map(metaText).filter(Boolean).join(', ');
    if (typeof v === 'object') {
        const o = v;
        return metaText(o.name || o.label || o.value || o.text || '');
    }
    return String(v).trim();
};
export function metaRecords(p) {
    return (Array.isArray(p?.providers) ? p.providers : []).map((x) => {
        let n = {};
        try {
            n = typeof x.normalized === 'string' ? JSON.parse(x.normalized || '{}') : (x.normalized ?? {});
        }
        catch { /* keep {} */ }
        const norm = n && typeof n === 'object' ? n : {};
        const d = norm.details && typeof norm.details === 'object' ? norm.details : {};
        return { provider: String(x.provider || 'Provider'), normalized: norm, details: d };
    });
}
const firstOf = (recs, keys) => {
    const out = [], seen = new Set();
    for (const r of recs)
        for (const o of [r.details, r.normalized])
            for (const k of keys) {
                const v = metaText(o?.[k]);
                if (v && !seen.has(v.toLowerCase())) {
                    seen.add(v.toLowerCase());
                    out.push(v);
                }
            }
    return out;
};
/** The first biography a provider supplied (used when the studio has no saved one). */
export const providerBio = (p) => firstOf(metaRecords(p), ['bio', 'biography', 'description', 'summary'])[0] ?? '';
const PLATFORM = { instagram: 'Instagram', twitter: 'X / Twitter', twitter_url: 'X / Twitter', x: 'X / Twitter', x_url: 'X / Twitter', youtube: 'YouTube', youtube_url: 'YouTube', tiktok: 'TikTok', facebook: 'Facebook', reddit: 'Reddit', onlyfans: 'OnlyFans', patreon: 'Patreon', threads: 'Threads', linktree: 'Linktree', linktr: 'Linktree' };
const HOSTS = [[/instagram\.com$/i, 'Instagram'], [/(^|\.)x\.com$/i, 'X / Twitter'], [/twitter\.com$/i, 'X / Twitter'], [/youtube\.com$/i, 'YouTube'], [/youtu\.be$/i, 'YouTube'], [/tiktok\.com$/i, 'TikTok'], [/facebook\.com$/i, 'Facebook'], [/reddit\.com$/i, 'Reddit'], [/onlyfans\.com$/i, 'OnlyFans'], [/patreon\.com$/i, 'Patreon'], [/threads\.net$/i, 'Threads'], [/linktr\.ee$/i, 'Linktree']];
const PROVIDER_HOST = /(stashdb|theporndb|porn-db|archive-x)/i;
const KEYS = new Set([...Object.keys(PLATFORM), 'social', 'socials', 'social_media', 'social_links', 'links', 'website', 'homepage', 'official_website', 'site_url', 'personal_website', 'profile', 'url']);
const KEY_HINT = /social|instagram|twitter|youtube|tiktok|facebook|reddit|onlyfans|patreon|threads|linktree/i;
const platformFrom = (key, host) => {
    const k = key.toLowerCase().replace(/[\s-]+/g, '_');
    for (const [needle, label] of Object.entries(PLATFORM))
        if (k === needle || k.includes(needle))
            return label;
    for (const [re, label] of HOSTS)
        if (re.test(host))
            return label;
    return '';
};
/** Public profile and social links found in provider details, plus the saved website; provider-site links are skipped. */
export function socialLinks(entity, p) {
    const found = [], seen = new Set();
    const add = (key, value, depth = 0, inside = false) => {
        if (value === null || value === undefined || depth > 5)
            return;
        if (Array.isArray(value))
            return void value.forEach((v) => add(key, v, depth + 1, inside));
        if (typeof value === 'object') {
            const o = value;
            const url = o.url || o.href || o.link || o.website || o.profile;
            const label = String(o.platform || o.network || o.service || o.name || key);
            const hint = inside || /social|link|contact/i.test(key);
            if (url)
                add(label, url, depth + 1, hint);
            for (const [child, val] of Object.entries(o))
                if (!['url', 'href', 'link', 'website', 'profile', 'name', 'platform', 'network', 'service'].includes(child.toLowerCase()))
                    add(child, val, depth + 1, hint);
            return;
        }
        const raw = String(value).trim();
        if (!/^https?:\/\//i.test(raw))
            return;
        let u;
        try {
            u = new URL(raw);
        }
        catch {
            return;
        }
        const host = u.hostname.replace(/^www\./i, '');
        if (PROVIDER_HOST.test(host))
            return;
        let label = platformFrom(key, host);
        const k = key.toLowerCase();
        if (!label && /^(website|homepage|official_website|site_url|personal_website)$/.test(k))
            label = 'Website';
        if (!label && inside)
            label = 'Website';
        if (!label && /^(profile|url|link)$/.test(k))
            label = platformFrom('', host);
        if (!label || seen.has(u.href))
            return;
        seen.add(u.href);
        found.push({ label, href: u.href });
    };
    for (const r of metaRecords(p))
        for (const o of [r.details, r.normalized])
            for (const [key, val] of Object.entries(o ?? {}))
                if (KEYS.has(key.toLowerCase()) || KEY_HINT.test(key))
                    add(key, val, 0, /social|links|contact/i.test(key));
    if (entity.profile)
        add('profile', entity.profile);
    const site = String(entity.site_url || entity.url || '').trim();
    if (/^https?:\/\//i.test(site)) {
        let href = '';
        try {
            href = new URL(site).href;
        }
        catch { /* invalid */ }
        if (href && !found.some((x) => x.href === href))
            found.unshift({ label: 'Official website', href });
    }
    return found;
}
export function brandIcon(raw) {
    try {
        const h = new URL(raw).hostname.toLowerCase().replace(/^www\./, '');
        const is = (d) => h === d || h.endsWith(`.${d}`);
        if (is('t.me') || is('telegram.org'))
            return 'brand-telegram';
        if (is('instagram.com'))
            return 'brand-instagram';
        if (is('x.com') || is('twitter.com'))
            return 'brand-x';
        if (is('youtube.com') || h === 'youtu.be')
            return 'brand-youtube';
        if (is('tiktok.com'))
            return 'brand-tiktok';
        if (is('reddit.com'))
            return 'brand-reddit';
        if (is('facebook.com'))
            return 'brand-facebook';
        if (is('patreon.com'))
            return 'brand-patreon';
        if (is('onlyfans.com'))
            return 'brand-onlyfans';
        if (is('linktr.ee'))
            return 'brand-linktree';
    }
    catch { /* fall through */ }
    return 'brand-website';
}
export function careerRows(p) {
    const out = [], seen = new Set();
    for (const it of Array.isArray(p?.career_counts) ? p.career_counts : []) {
        const provider = String(it?.provider ?? '').trim(), count = Number(it?.count);
        if (!provider || !Number.isInteger(count) || count < 0 || seen.has(provider.toLowerCase()))
            continue;
        seen.add(provider.toLowerCase());
        out.push([`Scenes listed by ${provider}`, count.toLocaleString()]);
    }
    return out;
}
function aliasesOf(site, recs) {
    let local = [];
    if (Array.isArray(site.aliases))
        local = site.aliases;
    else if (typeof site.aliases === 'string' && site.aliases.trim()) {
        try {
            const parsed = JSON.parse(site.aliases);
            local = Array.isArray(parsed) ? parsed : [site.aliases];
        }
        catch {
            local = site.aliases.split(/[,;|]/);
        }
    }
    const all = local.concat(firstOf(recs, ['aliases', 'alternative_names', 'alias_list'])).map(metaText).map((x) => x.trim());
    return [...new Set(all.filter((x) => x && x.toLowerCase() !== site.name.trim().toLowerCase()))];
}
/** Label/value rows of the Details card; empty values are left out. */
export function detailRows(site, p, videoCount, linkedPerformers) {
    const recs = metaRecords(p);
    const first = (keys) => firstOf(recs, keys)[0] ?? '';
    const providers = [...new Set(recs.map((r) => r.provider.toUpperCase()).filter(Boolean))];
    const linked = site.provider_ids && site.provider_ids !== '{}' && !(typeof site.provider_ids === 'object' && !Object.keys(site.provider_ids).length);
    const rows = [
        ['Country', metaText(site.country) || first(['country', 'country_name'])], ['Parent studio', metaText(site.parent) || first(['parent', 'parent_studio', 'parent_name'])],
        ['Founded', metaText(site.founded_year) || first(['founded_year', 'founded', 'founding_year', 'start_year'])], ['Career start', first(['career_start_year', 'career_start', 'career_start_date'])],
        ['Career end', first(['career_end_year', 'career_end', 'career_end_date'])], ['Videos in Archive X', videoCount.toLocaleString()], ...careerRows(p),
        ['Linked performers', linkedPerformers.toLocaleString()], ['Aliases', aliasesOf(site, recs).join(' · ')], ['Metadata', providers.length ? providers.join(' · ') : linked ? 'Linked' : 'Local profile'],
    ];
    return rows.filter(([, v]) => String(v ?? '').trim());
}
export const relatedList = (r) => (Array.isArray(r) ? r : []).filter((x) => Number(x?.id) > 0 && !!String(x?.name || '').trim());
/** Studios that appear on this studio's videos, most shared first, joined to the studio catalogue. */
export function similarStudios(videos, currentName, all, limit = 40) {
    const counts = new Map();
    for (const v of videos)
        for (const s of Array.isArray(v.studios) ? v.studios : []) {
            const k = String(s ?? '').trim();
            if (k && k.toLowerCase() !== currentName.toLowerCase())
                counts.set(k, (counts.get(k) ?? 0) + 1);
        }
    return all.filter((x) => counts.has(String(x.name || ''))).map((x) => ({ ...x, count: counts.get(String(x.name)) })).sort((a, b) => b.count - a.count).slice(0, limit);
}
export const SIMILAR_EMPTY = 'No similar studios are available from the current catalogue relationships.';
export const suggestionToolbar = (name, kind = 'studio') => kind === 'studio'
    ? `Does each video belong to ${name}? Confirming links this studio and hides other studio suggestions for the video. Performer links are separate; use Edit Video or Scene Match Review for full scene identification.`
    : `Does each video belong to ${name}? Confirming links this performer and hides other performer suggestions for the video. Studio links are separate; use Edit Video or Scene Match Review for full scene identification.`;
export const suggestionQuestion = (name) => `Does this video belong to ${name}?`;
export const suggestionTitle = (s) => String(s.title || s.filename || `video ${s.message_id}`);
export const pending = (n) => `${n} pending`;
export const SUGGEST_EMPTY = { title: 'No pending suggestions', body: 'Run Metadata & Suggestion Scan from Operations to create new suggestions.' };
export const confirmBody = (id, kind = 'studio') => ({ entity_type: kind, entity_id: id });
export const confirmPath = (sid) => `/api/suggestions/${sid}/confirm-entity`;
export const rejectPath = (sid) => `/api/suggestions/${sid}/reject-entity`;
export const yesLabel = (kind) => (kind === 'studio' ? 'Yes, link studio' : 'Yes, link performer');
export const linkedText = (kind) => (kind === 'studio' ? 'Studio linked to video.' : 'Performer linked to video.');
export const rejectedText = (kind) => (kind === 'studio' ? 'Not linked to this studio. Kept available for full scene review.' : 'Not linked to this performer. Kept available for full scene review.');
export const LINKED = linkedText('studio');
export const REJECTED = rejectedText('studio');
export const failedConfirm = (why) => `Could not confirm: ${why}`;
export const failedReject = (why) => `Could not reject: ${why}`;
export const succeeded = (r) => !!r && r._ok !== false && r.ok !== false;
export const suggestionsTab = (n) => (n ? `Suggestions ${n}` : 'Suggestions');
export { plural };

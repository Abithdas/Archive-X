import { studioChoices } from './mapping';
export const providerLabel = (p) => (p === 'stashdb' ? 'StashDB' : 'ThePornDB');
/** Local performers first, then provider ("API") matches that carry an id, de-duplicated; at most 28 rows. */
export function performerChoices(local, provider) {
    const localItems = local.map((p) => ({ name: p.name, id: p.id, count: p.video_count ?? 0, image: p.image ?? null, kind: 'local' }));
    const seen = new Set();
    const apiItems = [];
    for (const p of provider) {
        if (!p.name || !p.provider_id)
            continue;
        const key = `${p.provider}:${p.provider_id}`;
        if (seen.has(key))
            continue;
        seen.add(key);
        apiItems.push({ name: p.name, id: null, count: 0, image: p.image ?? null, kind: 'provider', provider: p.provider, provider_id: p.provider_id, profile: p.profile ?? null, exact: p.exact, alias_exact: p.alias_exact });
    }
    return [...localItems, ...apiItems].slice(0, 28);
}
export const performerNote = (c) => c.kind === 'provider'
    ? `API match · ${providerLabel(c.provider)}${c.exact ? ' · exact name' : c.alias_exact ? ' · alias match' : ''}`
    : `Local performer · ${c.count} video${c.count === 1 ? '' : 's'} · link to this performer`;
/** Provider search only starts at two characters. */
export const wantsProviderSearch = (q) => q.trim().length >= 2;
const performerFields = (p) => ({ performer_id: p.id, performer_name: p.name.trim(), provider: p.provider ?? null, provider_id: p.provider_id ?? null, image: p.image ?? null, profile: p.profile ?? null });
const studioFields = (s) => ({ site_id: s.id, studio_name: s.name.trim() });
export const performerBody = (picked, p) => ({ videos: picked, field: 'performer', performer_id: p.id, target_name: p.name.trim(), provider: p.provider ?? null, provider_id: p.provider_id ?? null, image: p.image ?? null, profile: p.profile ?? null, action: 'move', lock: true });
export const studioBody = (picked, s) => ({ videos: picked, field: 'site', site_id: s.id, target_name: s.name.trim(), action: 'move', lock: true });
/** "Map performer + studio" needs both. "Apply all metadata" needs at least one of title, performer, studio. */
export const canSave = (mode, c) => (mode === 'all' ? Boolean(c.title.trim() || c.performer || c.studio) : Boolean(c.performer && c.studio));
export const saveProblem = (mode) => (mode === 'all' ? 'Enter a title, or choose a performer or a studio.' : 'Select both a performer and a studio.');
/** Title first (if any), then the performer/studio part: both together, or just the one that was chosen. */
export function comboSteps(picked, c) {
    const steps = [];
    const title = c.title.trim();
    if (title)
        steps.push({ kind: 'title', body: { videos: picked, field: 'title', target_name: title, action: 'move', lock: true }, failure: 'Title update failed.' });
    const { performer: p, studio: s } = c;
    if (p || s) {
        const field = p && s ? 'performer_site' : p ? 'performer' : 'site';
        steps.push({ kind: 'mapping', failure: 'Combined mapping failed.', body: { videos: picked, field, action: 'move', lock: true, target_name: (p ?? s).name.trim(), ...(p ? performerFields(p) : {}), ...(s ? studioFields(s) : {}) } });
    }
    return steps;
}
export const n = (changed, fallback) => Number(changed ?? fallback) || fallback;
const vids = (k) => `${k} video${k === 1 ? '' : 's'}`;
export const performerLinked = (name, changed) => `${name} linked to ${changed} selected video${changed === 1 ? '' : 's'}.`;
export const studioLinked = (name, changed) => `${name} linked to ${vids(changed)}.`;
export const comboDone = (changed, hadTitle, field) => `${vids(changed)} ${hadTitle ? 'updated' : `mapped to ${field === 'performer_site' ? 'performer + studio' : field === 'performer' ? 'performer' : 'studio'}`}.`;
export const titleOnlyDone = (changed) => `Title applied to ${vids(changed)}.`;
export const heading = (mode) => ({ performer: 'Select performer', site: 'Select studio', both: 'Map performer + studio', all: 'Apply metadata' })[mode];
export const hint = (mode) => (mode === 'all' ? 'Change any of: title, performer, studio. Empty parts are left as they are.' : 'Choose both values, then save once.');
export const saveLabel = (mode) => (mode === 'all' ? 'Apply to selected videos' : 'Save performer + studio');
export const studioHits = (hits) => (Array.isArray(hits) ? hits : []);
export { studioChoices };

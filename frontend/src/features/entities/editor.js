export const isPerformer = (k) => k === 'performers';
export const titleFor = (k) => (isPerformer(k) ? 'Profile picture' : 'Studio image');
export const saveLabel = (k) => (isPerformer(k) ? 'Save profile picture' : 'Save studio image');
export const editUrl = (k, id) => `/api/${k}/${id}/edit`;
export const candidatesUrl = (k, id, offset, limit = PAGE) => `/api/${k}/${id}/image-candidates?limit=${limit}&offset=${offset}`;
export const imageUrl = (k, id, v) => `/api/${k}/${id}/image?v=${encodeURIComponent(String(v ?? ''))}`;
export const PAGE = 10;
const LABELS = [['theporndb', 'ThePornDB'], ['stashdb', 'STASHDB']];
/** Provider images grouped ThePornDB first, then StashDB; each tile says where it is from and how big it is. */
export function groups(cands) {
    return LABELS.map(([key, label]) => ({
        key, label,
        items: cands.map((c, index) => ({ c, index })).filter((x) => String(x.c.provider ?? '').toLowerCase() === key).map(({ c, index }) => ({ index, c, caption: `${label}${c.width && c.height ? ` · ${c.width}×${c.height}` : ''}` })),
    })).filter((g) => g.items.length);
}
export function providerSummary(cands, hasMore) {
    if (!cands.length)
        return 'No provider images found.';
    const counts = cands.reduce((m, c) => { const p = String(c.provider || 'provider').toUpperCase(); m[p] = (m[p] || 0) + 1; return m; }, {});
    return `${Object.entries(counts).map(([p, n]) => `${p}: ${n}`).join(' · ')}${hasMore ? ' · More images available' : ''}`;
}
export const footerFor = (n) => (n ? 'Select an image, then save.' : 'You can use a manual image URL or file.');
export const LOADING = 'Loading provider images…';
export const lookupFailed = (m) => `Provider image lookup failed: ${m}`;
export const providerLabel = (p) => String(p || 'provider').toUpperCase();
export const MAX_BYTES = 8 * 1024 * 1024;
export const fileProblem = (size) => (size > MAX_BYTES ? 'Image must be 8 MiB or smaller.' : '');
export const initialState = (locked) => (locked ? 'Locked current choice' : 'Current provider image');
export const STATE_LOCAL = 'Local image selected — save to apply';
export const STATE_RESET = 'Provider image will be restored on save';
export const stateProvider = (p) => `Selected ${providerLabel(p)} image — save to apply`;
/** A typed URL wins over a chosen provider image and over "restore"; an uploaded file wins over a provider image. */
export function saveBody(f) {
    const url = f.manualUrl.trim();
    const reset = url ? false : f.reset;
    const provider = url ? null : f.provider;
    const body = { name: f.name.trim(), image_url: url, reset_image_to_api: reset };
    if (isPerformer(f.kind)) {
        body.profile = f.profile.trim();
        body.bio = f.bio.trim();
        if (f.gender !== undefined)
            body.gender = f.gender;
    }
    if (f.imageData)
        body.image_data = f.imageData;
    if (!f.imageData && !url && !reset && provider?.url) {
        body.provider_image_url = provider.url;
        body.provider_image_provider = provider.provider || 'provider';
    }
    return body;
}
export const nameProblem = (n) => (n.trim() ? '' : 'Enter a name.');
export function savedMessage(f) {
    const url = f.manualUrl.trim();
    if (f.provider && !f.imageData && !url)
        return 'Selected profile image saved.';
    return f.reset && !url ? 'Provider profile image restored.' : 'Profile picture saved.';
}
export const saveFailed = (d) => `Save failed: ${d || 'error'}`;
export const loadFailed = (d) => `Could not load entity: ${d || 'error'}`;

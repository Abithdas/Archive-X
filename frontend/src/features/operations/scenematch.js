export const PAGE_SIZE = 24;
export const MAX_SELECTION = 500;
export const FRAMES = [0, 11, 23, 35];
export const DEFAULT_THRESHOLD = 95;
export const NO_FILTERS = { channel: '', field: 'all' };
export const isFiltered = (f) => !!f.channel || f.field !== 'all';
export const reviewUrl = (f, so, uo) => `/api/metadata/scene-match-review?${new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(so), unresolved_limit: String(PAGE_SIZE), unresolved_offset: String(uo), unresolved_channel_id: f.channel, unresolved_missing_field: f.field })}`;
export const clamp100 = (v) => Math.max(0, Math.min(100, Number(v) || 0));
export const thresholdOf = (v) => Math.max(0, Math.min(100, Number(v ?? DEFAULT_THRESHOLD)));
export const countText = (s, u, filtered) => `${s.toLocaleString()} suggestion${s === 1 ? '' : 's'} · ${u.toLocaleString()} need metadata${filtered ? ' · filtered' : ''}`;
export const visibleText = (shown, total) => `${shown.toLocaleString()} of ${total.toLocaleString()}`;
export const needs = (fields) => (fields ?? []).map((f) => (f === 'performer' ? 'performer' : 'studio'));
export const missingLabel = (fields) => (needs(fields).length ? needs(fields).join(' · ') : 'scene identity');
/** Joins the pages loaded so far into one list per kind, without repeating an item a later page also returned. */
export function merge(pages) {
    const sid = new Set(), mid = new Set();
    const suggestions = [], unresolved = [];
    for (const p of pages) {
        for (const s of p.items ?? [])
            if (!sid.has(s.suggestion_id)) {
                sid.add(s.suggestion_id);
                suggestions.push(s);
            }
        for (const u of p.unresolved_items ?? [])
            if (!mid.has(u.media_id)) {
                mid.add(u.media_id);
                unresolved.push(u);
            }
    }
    const last = pages[pages.length - 1];
    return { suggestions, unresolved, total: Number(last?.count) || 0, unresolvedTotal: Number(last?.unresolved_count) || 0, hasMore: Boolean(last?.has_more || last?.unresolved_has_more), channels: pages[0]?.unresolved_channels ?? [] };
}
/** Offsets for the next page: how many of each kind were already returned. */
export const nextOffsets = (pages) => ({ so: pages.reduce((n, p) => n + (p.items?.length ?? 0), 0), uo: pages.reduce((n, p) => n + (p.unresolved_items?.length ?? 0), 0) });
/** The redundant "reason" is shown only when it adds something beyond the match signals. */
export function distinctReason(signals, reason) {
    const norm = (t) => String(t ?? '').toLowerCase().replace(/\s+/g, ' ').trim();
    const s = norm(signals), r = norm(reason);
    return !!r && r !== s && !s.includes(r);
}
export const safeHttp = (u) => (/^https?:\/\//i.test(String(u ?? '')) ? String(u) : '');
export const providerBadge = (s) => String(s.provider ?? '').trim();
export const approveUrl = (id) => `/api/suggestions/${id}/confirm`;
export const rejectUrl = (id) => `/api/suggestions/${id}/reject`;
export const oneDone = (a) => (a === 'approve' ? 'Scene match approved.' : 'Scene suggestion rejected.');
export const oneFailed = (a) => `Could not ${a} this suggestion.`;
export const selectLimit = `Select up to ${MAX_SELECTION} suggestions at once.`;
export const rejectedToast = (r) => `Rejected ${Number(r.rejected || 0)} selected suggestion(s); ${Number(r.not_pending || 0)} were no longer pending; ${Number(r.failed || 0)} failed.`;
export const approveAboveCopy = (t) => ({ title: 'Approve high-confidence scenes', ok: 'Approve candidates', text: `Approve all pending candidates scored ${t.toFixed(1)}% or higher? Performer conflicts and safety checks will still block approval.` });
export function approvedToast(r) {
    const n = (k) => Number(r[k] || 0);
    return `Approved ${n('approved')}; ${n('ambiguous_skipped')} ambiguous video(s) left for review; ${n('website_variants_skipped')} cross-site variant(s) need individual review; ${n('performer_conflicts')} blocked by performer conflicts.`;
}
export const retryToast = (r) => (r.id ? `Retry queued for ${Number(r.count || 0).toLocaleString()} video(s).` : r.message || 'No unmatched videos found.');
export const retryStatus = (r) => r.message || `Queued ${Number(r.count || 0).toLocaleString()} unmatched/suggested video(s) for one manual recheck.`;
/** A video-card-shaped object for the quick editor, from a catalogue entry that needs metadata. */
export const editItem = (u) => ({ id: Number(u.message_id), _cid: String(u.channel_id ?? ''), media_id: u.media_id, name: u.title || u.filename || 'Video', filename: u.filename || '', size: null, duration: Number(u.duration) || 0, width: null, height: null, resolution_category: null, thumbnail_id: Number(u.message_id), favorite: 0, upload_date: null, channel_title: u.channel_title ?? null, performers: u.performers ?? [], studios: u.studio_name ? [u.studio_name] : [], tags: [], studio: u.studio_name || '' });

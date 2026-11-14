/** Pure logic for the channel duplicate-video review (mirrors the classic dialog's rules). */
export const ALL_INDEXED = '__all_indexed__';
export const BATCH_LIMIT = 20;
export const SNAPSHOT_FRAMES = [0, 11, 23, 35];
export const CHOOSE_SCOPE = 'Choose a channel or All indexed videos to scan first.';
export const SCAN_FAILED = 'The channel duplicate scan failed. No messages were changed.';
export const ALL_FAILED = 'The cross-channel duplicate review failed. No messages were changed.';
export const DELETE_FAILED = 'The delete request failed.';
export const candidateKey = (r) => `${String(r.channel_id ?? '')}:${Number(r.message_id ?? 0)}`;
export function formatSize(value) {
    const n = Math.max(0, Number(value) || 0);
    if (n < 1024)
        return `${n} B`;
    if (n < 1024 ** 2)
        return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 ** 3)
        return `${(n / 1024 ** 2).toFixed(1)} MB`;
    return `${(n / 1024 ** 3).toFixed(2)} GB`;
}
export const formatDuration = (s) => { const d = Number(s) || 0; return d > 0 ? `${Math.floor(d / 60)}:${String(Math.floor(d % 60)).padStart(2, '0')}` : 'Unknown'; };
export const formatResolution = (w, h) => (Number(w) > 0 && Number(h) > 0 ? `${Number(w)}×${Number(h)}` : 'Unknown');
/** Display name: a real title, else the saved channel's title, else "Unknown channel". */
export function channelName(id, title, saved = []) {
    const real = (v) => { const t = String(v ?? '').trim(); return !!t && !/^unknown channel$/i.test(t) && !/^channel\s+-?\d+$/i.test(t) && !/^-?\d+$/.test(t); };
    if (real(title))
        return String(title).trim();
    const raw = String(id ?? '').trim();
    const legacy = /^channel\s+(-?\d+)$/i.exec(raw);
    const key = legacy ? legacy[1] : raw;
    const hit = saved.find((c) => String(c.telegram_id ?? '').trim() === key);
    if (real(hit?.title))
        return String(hit.title).trim();
    if (raw && !legacy && !/^-?\d+$/.test(raw))
        return raw;
    return 'Unknown channel';
}
/** Scope options: unique numeric ids only, sorted by name, preceded by the cross-channel choice. */
export function scopeOptions(channels) {
    const seen = new Map();
    for (const c of channels) {
        const id = String(c?.telegram_id ?? '').trim();
        if (!/^-?\d+$/.test(id) || seen.has(id))
            continue;
        seen.set(id, channelName(id, c.title, channels));
    }
    const items = [...seen].map(([value, label]) => ({ value, label })).sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: 'base' }));
    return [{ value: '', label: 'Choose a scan scope…' }, { value: ALL_INDEXED, label: 'All indexed videos · local review' }, ...items];
}
const docId = (r) => { const id = String(r.document_id ?? '').trim(); const k = String(r.registry_key ?? ''); return id || (k.startsWith('doc:') ? k.slice(4) : ''); };
const hash = (r) => String(r.oshash || r.registry_oshash || r.uploaded_oshash || '').trim().toLowerCase();
/** Two copies are the same video only by Telegram document id, or by OSHASH plus an identical non-zero size. */
export function sharesIdentity(a, b) {
    const da = docId(a);
    if (da && da === docId(b))
        return true;
    const h = hash(a);
    const sa = Number(a.size || 0);
    return !!h && h === hash(b) && sa > 0 && sa === Number(b.size || 0);
}
/** An unselected copy in the same group that directly matches `target`, or null. */
export function findKeeper(group, target, selected) {
    const tk = candidateKey(target);
    return (group.candidates ?? []).find((c) => candidateKey(c) !== tk && !selected.has(candidateKey(c)) && sharesIdentity(target, c)) ?? null;
}
export function selectedTargets(groups, keys) {
    return groups.flatMap((group) => (group.candidates ?? []).filter((r) => keys.has(candidateKey(r))).map((row) => ({ group, row, key: candidateKey(row) })));
}
export function selectionState(groups, keys, locked = false) {
    const sel = selectedTargets(groups, keys);
    const sk = new Set(sel.map((t) => t.key));
    const channels = new Set(sel.map((t) => String(t.row.channel_id ?? ''))).size;
    const missingKeeper = sel.some((t) => !findKeeper(t.group, t.row, sk));
    const countText = `${sel.length} selected${channels ? ` · ${channels} channel${channels === 1 ? '' : 's'}` : ''}`;
    const hint = sel.length > BATCH_LIMIT ? `Select no more than ${BATCH_LIMIT} videos per batch.`
        : missingKeeper ? 'Leave one directly matching copy unselected in each group before deleting.'
            : sel.length ? 'Each selected copy will be checked against a retained match before deletion.'
                : 'Select copies to delete; leave one directly matching copy in each group.';
    return { count: sel.length, channels, missingKeeper, countText, hint, canDelete: !!sel.length && sel.length <= BATCH_LIMIT && !missingKeeper && !locked, canSelectMore: sel.length < BATCH_LIMIT };
}
export function deletePlan(groups, keys, saved = []) {
    const sel = selectedTargets(groups, keys);
    const sk = new Set(sel.map((t) => t.key));
    const plan = [];
    for (const t of sel) {
        const keeper = findKeeper(t.group, t.row, sk);
        if (!keeper)
            return null;
        const mid = Number(t.row.message_id || 0);
        const title = String(t.row.title || t.row.filename || `Video ${mid}`);
        plan.push({ row: t.row, keeper, label: `• ${title} — ${channelName(t.row.channel_id, t.row.channel_title, saved)}, message ${mid}, ${Number(t.row.width || 0)}×${Number(t.row.height || 0)}, ${formatSize(t.row.size)}` });
    }
    return plan;
}
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
export function confirmCopy(plan) {
    const channels = new Set(plan.map((p) => String(p.row.channel_id ?? ''))).size;
    return {
        title: `Delete ${plural(plan.length, 'selected video')}?`,
        ok: `Delete ${plural(plan.length, 'video')}`,
        text: `Permanently delete ${plural(plan.length, 'selected video')} from ${plural(channels, 'Telegram channel')} and remove them from the Archive X library?\n\n${plan.map((p) => p.label).join('\n')}\n\nEach video will be checked against an unselected copy with a directly matching Telegram document ID or saved OSHASH + file size before it is deleted. A linked thumbnail is deleted only if it is verified as an unshared image for that video; missing or unsafe thumbnails are left alone. A batch can stop partway if a live check fails. This cannot be undone.`,
        channels,
    };
}
export const deleteBody = (p) => ({ confirm_delete: true, channel_id: Number(p.row.channel_id || 0), message_id: Number(p.row.message_id || 0), keep_channel_id: Number(p.keeper.channel_id || 0), keep_message_id: Number(p.keeper.message_id || 0) });
export const validIds = (b) => !!b.channel_id && !!b.keep_channel_id && b.message_id > 0 && b.keep_message_id > 0;
export const progressText = (i, total, channel, mid) => `Deleting ${i + 1} of ${total}: ${channel} · message ${mid}…`;
export const resultWarnings = (r) => [...(r.thumbnail_deleted ? [`Thumbnail ${r.thumbnail_message_id} deleted`] : []), ...(Array.isArray(r.warnings) ? r.warnings : [])];
export const doneToast = (deleted, channels, warnings) => `Deleted ${plural(deleted, 'selected video')} across ${plural(channels, 'channel')}.${warnings.length ? ` ${warnings.slice(0, 3).join(' · ')}` : ''}`;
export const stoppedToast = (deleted, total, channel, mid, why) => `Deleted ${deleted} of ${total}; stopped before ${channel || 'the next video'} · message ${mid}. ${why}`;
export const stoppedStatus = (deleted, total, why) => `Batch stopped after ${deleted} of ${plural(total, 'deletion')}. ${why}`;
export function scanTitles(scan, saved = []) {
    const all = scan.scope === 'all_indexed';
    return { title: all ? 'Duplicate videos across channels' : `Duplicate videos in ${channelName(scan.channel_id, scan.channel_title, saved)}`, context: all ? 'Cross-channel matches · select copies to delete after review' : 'Verified matches · select unwanted copies to delete' };
}
export const introText = (all) => all
    ? 'Only the same saved Telegram document ID or OSHASH plus file size counts as a duplicate. Similar filenames are not proof. Select the copies you want removed; each will be checked against an unselected matching copy before deletion. Missing fingerprints are not fetched automatically.'
    : 'Only exact Telegram document IDs or matching OSHASH plus file size count as duplicates. Select unwanted copies and leave one directly matching copy in each group. Archive X verifies each selected copy before deletion.';
export const scanningText = (all) => all ? 'Reading saved identities across indexed videos. No messages are changed during the scan.' : 'Scanning this channel’s indexed videos. Un-hashed candidates may be fingerprinted as part of this explicit scan; no messages are deleted during a scan.';
export function scanSummary(scan) {
    const groups = scan.groups ?? [];
    const copies = groups.reduce((n, g) => n + (g.candidates?.length ?? 0), 0);
    const all = scan.scope === 'all_indexed';
    const count = Number(scan.indexed_video_count || 0);
    const detail = all
        ? `${count.toLocaleString()} indexed videos across ${Number(scan.indexed_channel_count || 0).toLocaleString()} channels · ${Number(scan.complete_channel_count || 0).toLocaleString()} channels with complete scans`
        : `${count.toLocaleString()} indexed videos · ${Number(scan.hashes_computed || 0).toLocaleString()} files fingerprinted`;
    const warnings = [scan.warning, ...(scan.fingerprint_warnings ?? []).slice(0, 4).map(String)].filter((w) => !!w);
    return { groups: groups.length, copies, all, detail, warnings, state: warnings.length ? 'Scan complete · check coverage' : 'Scan complete', emptyNote: count ? 'The scan found no repeated Telegram document IDs or matching OSHASH content fingerprints.' : all ? 'No active indexed video records were found.' : 'No active indexed videos were found for this channel.' };
}
export const scanUrl = (scope) => (scope === ALL_INDEXED ? '/api/channel-maintenance/duplicates/all' : `/api/channel-maintenance/duplicates?channel_id=${encodeURIComponent(scope)}`);
export const frameUrl = (cid, mid, frame, rev = '') => `/api/play/preview-frame/${encodeURIComponent(String(cid))}/${encodeURIComponent(String(mid))}/${frame}${rev ? `?rev=${encodeURIComponent(rev)}` : ''}`;

export const ENTITY_STAGES = ['metadata', 'performers', 'channel_data'];
export const STAGE_ORDER = ['inventory', 'metadata', 'performers', 'channel_data', 'snapshots'];
const LABELS = { inventory: 'Inventory', metadata: 'Metadata & Entity Data', performers: 'Metadata & Entity Data', channel_data: 'Metadata & Entity Data', snapshots: 'Build snapshots' };
export const DEFAULT_OPTIONS = { inventory: true, entities: true, snapshots: true, scope: 'all', forceMetadata: false, forceEntity: false, fullInventory: false, imageScan: false, photoReconcile: false };
/** The three visible stage switches → concrete stage ids (entity data always runs as one pipeline). */
export function selectedStages(o, forPending = false) {
    const out = [];
    if (o.inventory && !forPending)
        out.push('inventory');
    if (o.entities)
        out.push(...ENTITY_STAGES);
    if (o.snapshots)
        out.push('snapshots');
    return out;
}
export function expandStages(stages) {
    const sel = new Set(stages);
    if (ENTITY_STAGES.some((x) => sel.has(x)))
        ENTITY_STAGES.forEach((x) => sel.add(x));
    return STAGE_ORDER.filter((x) => sel.has(x));
}
export function stageLabels(stages) {
    const out = [];
    for (const st of expandStages(stages))
        if (!out.includes(LABELS[st]))
            out.push(LABELS[st]);
    return out;
}
export function scopeBody(o) {
    return o.scope === 'all' ? { limit: 50, unbounded: true } : { limit: Math.max(25, Number(o.scope) || 50), unbounded: false };
}
/** Full run. Full inventory also reconciles delivered-post captions, as in the classic UI. */
export function processBody(channels, stages, o, autoAdded = {}) {
    return { channel_ids: channels, stages, ...scopeBody(o), force_metadata: o.forceMetadata, force_entity: o.forceEntity, full_inventory: o.fullInventory, reconcile_uploaded_captions: o.fullInventory, auto_added_prerequisites: autoAdded };
}
export const pendingBody = (channels, o) => ({ channel_ids: channels, stages: selectedStages(o, true), ...scopeBody(o), force_metadata: o.forceMetadata, force_entity: o.forceEntity });
/** What the preflight asks to add before the run: stages to add, and which channels lacked which stage. */
export function prerequisitePlan(pf, chosen) {
    const required = pf.required_stages ?? [];
    const autoAdded = {};
    for (const item of pf.missing_by_channel ?? [])
        for (const st of item.missing ?? [])
            (autoAdded[st] ??= []).push(String(item.channel_id));
    return { required, labels: stageLabels(required), affected: (pf.missing_by_channel ?? []).map((x) => String(x.channel_id)), stages: expandStages([...required, ...chosen]), autoAdded };
}
export const photoMode = (o) => (o.imageScan && o.photoReconcile ? 'both' : o.imageScan ? 'scan' : o.photoReconcile ? 'reconcile' : null);
/** One job covers "both": the scan finishes first, then reconciliation checks the newly indexed message IDs (no race between two jobs). */
export const photoRequest = (mode, channels) => mode === 'scan'
    ? { url: '/api/photos/scan', body: { channel_ids: [...channels] } }
    : { url: '/api/photos/reconcile', body: mode === 'both' ? { channel_ids: [...channels], also_scan: true } : { channel_ids: [...channels] } };
export const photoFailure = (mode, why) => `${mode === 'both' ? 'Image scan + reconciliation' : mode === 'scan' ? 'Image scan' : 'Photo reconciliation'} could not be queued: ${why || 'unknown error'}`;
export function photoLaunch(mode, id, channels) {
    const n = `${channels} selected channel${channels === 1 ? '' : 's'}`;
    return mode === 'both' ? `Image scan followed by deleted-image reconciliation queued as job ${id} for ${n}.`
        : mode === 'scan' ? `Image scan queued as job ${id} for ${n}; it scans full Telegram history without modifying messages.`
            : `Deleted Telegram image reconciliation queued as job ${id}; it checks already-indexed message IDs only and does not rescan channel history.`;
}
export const joinLaunch = (meta, photo) => [meta, photo].filter(Boolean).join('. ');

const s = (v) => String(v ?? '');
const num = (v) => (Number.isFinite(Number(v)) ? Number(v) : 0);
export function laneChips(gov, res) {
    const bits = [];
    if (gov.telegram_file_active_small != null)
        bits.push(`${s(gov.telegram_file_active_small)}/${gov.telegram_file_limit_small ?? '—'} small-file slots`);
    if (gov.telegram_file_active_large != null)
        bits.push(`${s(gov.telegram_file_active_large)}/${gov.telegram_file_limit_large ?? '—'} large-file slots`);
    return [
        { key: 'uploads', label: 'Uploads / transfers', value: `${gov.transfer_active ?? 0}/${gov.transfer_capacity ?? 2}` },
        { key: 'downloads', label: 'Telegram downloads', value: bits.join(' · ') || 'shared file-count limits' },
        { key: 'playback', label: 'Playback', value: gov.playback_active ? 'priority active' : 'idle' },
        { key: 'workers', label: 'CPU/disk workers', value: `${res.background_active ?? 0}/${res.background_capacity ?? '—'}` },
        { key: 'media', label: 'Media workers', value: `${res.media_workers ?? '—'}` },
    ];
}
const LOCAL = ['local', 'local_files', 'upload_local'];
export function laneName(type, source, workflow) {
    if (s(workflow).toUpperCase() === 'CONTENT_AUTOMATION')
        return 'Content Automation · delivery';
    if (s(type) === 'TELEGRAM_TRANSFER' && LOCAL.includes(s(source).toLowerCase()))
        return 'Local → channel upload';
    if (s(type) === 'TELEGRAM_TRANSFER')
        return 'Telegram → Telegram transfer';
    const names = { UPLOAD_LOCAL: 'Local → channel upload', MEDIA_MANAGER: 'Telegram Media Manager', METADATA_CHANNEL_PIPELINE: 'Metadata & Assets scan', METADATA_ASSETS: 'Metadata & Assets', CONTENT_AUTOMATION: 'Content Automation' };
    return s(names[s(type)]) || s(type) || 'Background job';
}
export function laneKind(type, workflow) {
    const t = s(type).toUpperCase();
    if (t === 'CONTENT_AUTOMATION' || s(workflow).toUpperCase() === 'CONTENT_AUTOMATION')
        return 'automation';
    return ['METADATA_ASSETS', 'METADATA_CHANNEL_PIPELINE', 'MEDIA_MANAGER'].includes(t) ? 'metadata' : 'transfer';
}
export const ACTIVE = ['RUNNING', 'PAUSING', 'PAUSED', 'WAITING', 'CANCEL_REQUESTED'];
export const TERMINAL = ['COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED', 'CANCELLED', 'ERROR'];
export const activeLaneCount = (jobs) => jobs.filter((j) => ACTIVE.includes(s(j.status).toUpperCase())).length;
/** One card per lane job. `nameOf` turns channel ids into titles. */
export function toLanes(jobs, nameOf) {
    return jobs.map((j, index) => {
        const type = s(j.type).toUpperCase();
        const op = s(j.operation) || 'Queued';
        const status = s(j.status || 'QUEUED').toUpperCase();
        const kind = laneKind(j.type, j.workflow);
        const local = LOCAL.includes(s(j.source).toLowerCase());
        const shortName = kind === 'automation' ? 'Automation' : kind === 'metadata' ? 'Metadata' : type === 'UPLOAD_LOCAL' || (type === 'TELEGRAM_TRANSFER' && local) ? 'Local upload' : 'Transfer';
        const file = s(j.current_item).split(/[\\/]/).pop().replace(/\s·\s\d+(?:\.\d+)?%$/, '');
        const from = nameOf(j.source_channel);
        const to = nameOf(j.destination_channel);
        const title = kind === 'metadata' ? ['Channel scan', from].filter(Boolean).join(' · ') : file ? `${/upload/i.test(op) ? 'Uploading ' : ''}${file}` : shortName;
        const subtitle = [laneName(j.type, j.source, j.workflow), j.destination_channel ? `to ${to}` : '', j.source_channel && kind !== 'metadata' ? `from ${from}` : '', kind === 'metadata' || !file ? op : ''].filter(Boolean).join(' · ');
        const id = s(j.id);
        const interrupted = ['INTERRUPTED', 'RECOVERING'].includes(status);
        const paused = ['PAUSED', 'PAUSING'].includes(status);
        const terminal = TERMINAL.includes(status);
        const stopping = status === 'CANCEL_REQUESTED';
        const actions = !id || terminal || stopping ? [] : interrupted ? ['recover', 'cancel'] : paused ? ['resume', 'cancel'] : ['pause', 'cancel'];
        return { id, key: id || `${type}:${s(j.source)}:${index}`, kind, title, subtitle, detail: kind === 'metadata' ? file : '', pct: Math.max(0, Math.min(100, num(j.progress))), status, statusLabel: status.replaceAll('_', ' ').toLowerCase(), actions, stopping, waiting: s(j.metrics?.waiting_reason) };
    });
}
export const jobPath = (id, action) => `/api/jobs/${encodeURIComponent(id)}/${action}`;

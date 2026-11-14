const s = (v) => (v == null ? '' : String(v));
const num = (v) => { const n = Number(v); return Number.isFinite(n) ? n : 0; };
export function parseDetail(j) {
    const raw = j.detail ?? j.operation_detail ?? '';
    try {
        return typeof raw === 'string' && raw.trim().startsWith('{') ? JSON.parse(raw) : null;
    }
    catch {
        return null;
    }
}
export function progressSummary(j) {
    const total = num(j.total) > 0 ? num(j.total) : 0;
    const completed = Math.max(0, num(j.completed));
    const pv = num(j.progress);
    const raw = total > 0 ? (completed * 100) / total : pv;
    return { percent: Number.isFinite(raw) ? Math.max(0, Math.min(100, raw)) : 0, label: total > 0 ? `${completed.toLocaleString()} / ${total.toLocaleString()}` : `${pv.toFixed(1)}%` };
}
export const toneOf = (st) => (st === 'COMPLETED' ? 'good' : ['FAILED', 'ERROR'].includes(st) ? 'bad' : ['PAUSED', 'PAUSING'].includes(st) ? 'warn' : ['RUNNING', 'RECOVERING'].includes(st) ? 'accent' : 'muted');
export function laneName(type, source, workflow) {
    if (s(workflow).toUpperCase() === 'CONTENT_AUTOMATION')
        return 'Content Automation · delivery';
    if (type === 'TELEGRAM_TRANSFER' && ['local', 'local_files', 'upload_local'].includes(s(source).toLowerCase()))
        return 'Local → channel upload';
    if (type === 'TELEGRAM_TRANSFER')
        return 'Telegram → Telegram transfer';
    const names = { UPLOAD_LOCAL: 'Local → channel upload', MEDIA_MANAGER: 'Telegram Media Manager', METADATA_CHANNEL_PIPELINE: 'Metadata & Assets scan', METADATA_ASSETS: 'Metadata & Assets', CONTENT_AUTOMATION: 'Content Automation' };
    return names[type] ?? (type || 'Background work');
}
/** The heading: the job's own name, unless that is just its type, in which case the readable kind. */
export function displayName(j, id) {
    const parsed = parseDetail(j);
    const type = s(j.job_type || j.type);
    const kind = laneName(type, parsed?.source ?? j.source, parsed?.workflow ?? j.workflow);
    const raw = s(j.name).trim();
    return { name: (raw && raw.toUpperCase() !== type.toUpperCase() ? raw : kind) || `Job ${id}`, kind, type };
}
export const shortId = (id) => (id.length > 10 ? `${id.slice(0, 8)}…` : id);
const ACTIVE = ['RUNNING', 'PAUSED', 'PAUSING', 'RECOVERING', 'RETRYING', 'RESUMING', 'STARTING'];
const PATTERNS = {
    'Scan photos': /\bscan(?:ning)?\b.*\bphoto/i, 'Upload photos': /\bupload(?:ing)?\b.*\bphoto/i, Complete: /\bcomplete(?:d)?\b|\bfinished\b|\bprocessed photo/i,
    Inventory: /\binventory\b|\bcollect(?:ing)? files\b|\bscan(?:ning)? input\b/i, 'Duplicate check': /\bduplicate\b|\bdedup(?:e|lication)?\b|\bfingerprint/i, 'Metadata saved': /\bmetadata\b|\bscene match/i,
    Upload: /\bupload(?:ing)?\b|\btransfer(?:ring)?\b|\bsend(?:ing)?(?: media)?\b/i, 'Verify delivery': /\bverif(?:y|ying|ied|ication)\b|\bdelivery\b|\bconfirm(?:ing)? message\b/i, 'Clean up source': /\bclean(?:up|ing up)\b|\bdelete source\b|\bremove source\b/i,
    Queued: /\bqueued\b|\bwaiting\b/i, Dispatched: /\bdispatch(?:ed|ing)?\b|\bstarting\b/i, Processing: /\bprocess(?:ing|ed)?\b|\bcollect(?:ing)?\b|\bscan(?:ning)?\b/i, Verification: /\bverif(?:y|ying|ied|ication)\b|\bdelivery\b/i, Cleanup: /\bclean(?:up|ing up)\b|\bcleanup\b/i,
};
/** Which stage a job is in, from what it says it is doing (or, failing that, how far along it is). */
export function pipeline(j, status, percent) {
    const operation = [j.current_operation, j.current_step, j.progress_label].filter(Boolean).map(String).join(' ').toLowerCase();
    const type = s(j.job_type || j.type).toUpperCase();
    let detail = {};
    try {
        detail = typeof j.detail === 'string' ? JSON.parse(j.detail || '{}') : (j.detail || {});
    }
    catch { /* not JSON */ }
    const localUpload = type === 'UPLOAD_LOCAL' || (type === 'TELEGRAM_TRANSFER' && /^(?:local|local_files|upload_local)$/i.test(s(j.source || detail.source || detail.mode)));
    const photoUpload = localUpload && s(detail.media_type).toUpperCase() === 'PHOTOS';
    const base = photoUpload ? ['Scan photos', 'Upload photos', 'Complete'] : localUpload ? ['Inventory', 'Duplicate check', 'Metadata saved', 'Upload', 'Verify delivery', 'Clean up source'] : ['Queued', 'Dispatched', 'Processing', 'Verification', 'Cleanup'];
    let active = base.findIndex((label) => (PATTERNS[label] ?? new RegExp(label, 'i')).test(operation));
    if (active < 0 && ACTIVE.includes(status)) {
        active = Math.min(base.length - 1, Math.max(0, Math.floor(((Number(percent) || 0) / 100) * (base.length - 1))));
        if (active === 0 && base.length > 1)
            active = 1;
    }
    const stages = base.map((label, index) => {
        const isActive = index === active && ACTIVE.includes(status);
        const done = status === 'COMPLETED' || (index < active && active >= 0);
        const failed = ['FAILED', 'ERROR'].includes(status) && index === Math.max(0, active);
        return { label, state: failed ? 'failed' : done ? 'done' : isActive ? 'active' : 'todo' };
    });
    return { stages, completed: stages.filter((x) => x.state === 'done').length, total: stages.length };
}
export function runInfo(j, parsed, short, age) {
    const items = [];
    const add = (label, value) => { if (value !== undefined && value !== null && String(value).trim() !== '')
        items.push({ label, value: String(value) }); };
    const type = s(j.job_type || j.type) || '—';
    const retries = s(j.retry_count ?? j.retries ?? 0);
    if (/TELEGRAM_TRANSFER|UPLOAD_LOCAL|DOWNLOAD/i.test(type)) {
        const transfer = (parsed?.transfer ?? {});
        const inFlight = j.parts_in_flight ?? parsed?.parts_in_flight ?? transfer.parts_in_flight;
        if (inFlight !== undefined)
            add('Parts in flight', inFlight);
        const fw = j.upload_flood_wait_remaining ?? j.flood_wait_remaining ?? j.flood_wait ?? parsed?.upload_flood_wait_remaining ?? parsed?.flood_wait_remaining ?? parsed?.flood_wait;
        add('FloodWait', fw === undefined || fw === null || fw === '' || Number(fw) === 0 ? 'None' : Number.isFinite(Number(fw)) ? `${Math.ceil(Number(fw))}s` : String(fw));
        add('Retries', retries);
        add('Job ID', `#${short}`);
        return items;
    }
    const a = (v) => (v == null ? '—' : age(num(v)));
    const deps = Array.isArray(j.dependencies) && j.dependencies.length ? j.dependencies.map((d) => `${s(d.depends_on_job_id)}:${s(d.dependency_status) || 'UNKNOWN'}`).join(', ') : 'None';
    add('Type', type);
    add('Retries', retries);
    add('Admission', `${s(j.admission_state) || '—'}${j.admission_reason ? ` · ${s(j.admission_reason)}` : ''}`);
    add('Attempt', s(j.attempt ?? '—'));
    add('Worker', `${s(j.worker_id) || '—'} · ${s(j.worker_state) || '—'}`);
    add('Execution', s(j.execution_id) || '—');
    add('Lease', s(j.lease_id) || '—');
    add('Failure class', s(j.failure_class) || '—');
    add('Watchdog', `${s(j.watchdog_state) || 'UNKNOWN'}${j.watchdog_reason ? ` · ${s(j.watchdog_reason)}` : ''}`);
    add('Watchdog ages', `HB ${a(j.watchdog_heartbeat_age)} · Activity ${a(j.watchdog_activity_age)} · Progress ${a(j.watchdog_progress_age)}`);
    add('Dependencies', deps);
    add('Job ID', `#${short}`);
    return items;
}
/** Newest page replaces its own rows; older pages already loaded are kept; the result is oldest to newest. */
export function mergeActivity(older, latest) {
    const all = new Map([...older, ...latest]);
    return [...all.values()].sort((a, b) => num(a.id) - num(b.id));
}
export const activityKey = (x) => s(x.id ?? `${s(x.created_at)}|${s(x.job_id)}|${s(x.level)}|${s(x.message)}`);
/** Newest first, as the classic panel shows it. */
export function activityRows(items, fmtTime) {
    return [...items].reverse().map((x) => {
        const message = s(x.message || x.operation || x.event) || 'Activity';
        const operation = s(x.operation);
        let details = '';
        try {
            details = JSON.stringify(x.details ?? {}, null, 2);
        }
        catch {
            details = s(x.details);
        }
        const time = s(x.created_at);
        const meta = [time ? fmtTime(time) : '', x.job_id ? `· job ${s(x.job_id)}` : '', x.event ? `· ${s(x.event)}` : ''].filter(Boolean).join(' ');
        return { key: activityKey(x), level: s(x.level || 'INFO').toUpperCase(), message, operation: operation && operation !== message ? operation : '', meta, details };
    });
}
/** What the drawer's footer offers: terminal jobs nothing; otherwise one primary action (by status) and cancel. */
export function footerFor(status) {
    const st = status.toUpperCase();
    if (['COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED', 'ERROR', 'CANCELLED'].includes(st))
        return null;
    const primary = st === 'RECOVERING' ? 'retry-recovery' : st === 'INTERRUPTED' ? 'recover' : st === 'PAUSED' ? 'resume' : st === 'RUNNING' ? 'pause' : null;
    return { primary, cancel: st === 'CANCEL_REQUESTED' ? 'Cancelling…' : 'Cancel job' };
}
export const JOB_LOAD_FAILED = (m) => `Could not load job details: ${m}`;

export const LAST_KEY = 'ax-library-health-last-checked';
export const HEALTH_URL = '/api/operations/library-health?limit=100&stale_job_hours=24';
export const REPAIR_URL = '/api/operations/library-health/repair-artwork';
export const NOT_CHECKED = 'Run a check to review local previews, artwork caches and background jobs.';
export const NOT_CHECKED_DETAIL = 'Not checked. Scan for missing snapshots, empty artwork caches, and stale jobs.';
export const CHECKING = 'Checking local previews, artwork caches and background jobs…';
export const CHECK_FAILED_OVERVIEW = 'The check could not finish. Open the details for the error and try again.';
export const REPAIRING = 'Repairing artwork cache from matching local files only…';
export const num = (v) => Number(v || 0);
export const findingCount = (r) => num(r.summary?.finding_count ?? (r.findings ?? []).length);
export const stateOf = (r) => (findingCount(r) === 0 ? 'healthy' : 'review');
export const chipText = (s, r) => s === 'unchecked' ? 'Not checked' : s === 'checking' ? 'Checking…' : s === 'error' ? 'Check failed' : s === 'healthy' ? 'Healthy' : `${(r?.findings?.length || findingCount(r ?? {}) || 1)} finding${(r?.findings?.length || findingCount(r ?? {}) || 1) === 1 ? '' : 's'}`;
export const overviewText = (s) => s === 'healthy' ? 'Metadata, previews and background jobs look good. No issues were found.' : s === 'review' ? 'Review the reported findings below to repair missing previews, artwork or stalled jobs.' : s === 'error' ? CHECK_FAILED_OVERVIEW : s === 'checking' ? CHECKING : NOT_CHECKED;
/** The plain-text report shown under "View check details". */
export function reportLines(r) {
    const s = r.summary ?? {};
    const artwork = Object.values((s.artwork_cache_missing_bytes ?? {})).reduce((a, b) => a + num(b), 0);
    return [
        `Checked ${r.checked_at || 'now'}.`, `${num(s.active_media)} active media item(s).`, `${num(s.missing_snapshot_cache)} missing representative snapshot(s).`, `${num(s.empty_snapshot_blobs)} empty snapshot-cache row(s).`,
        `${num(s.missing_preview_files)} incomplete preview cache(s).`, `${num(s.stale_jobs)} stale background job(s).`, `Artwork cache misses: ${artwork}.`,
        ...(r.findings ?? []).map((f) => `${f.severity || 'info'} · ${f.code}: ${f.count} — ${f.message}`),
    ];
}
export const failedText = (why) => `Library health check failed: ${why}`;
export const repairToast = (r) => `Artwork cache: ${r.repaired || 0} repaired, ${r.skipped || 0} unavailable locally, ${r.failed || 0} errors.`;
export const REPAIR_FAILED = 'Artwork cache repair failed.';
export const repairFailedText = (why) => `Artwork cache repair failed: ${why}`;
export function lastChecked(value) {
    if (!value)
        return 'Never';
    const d = new Date(value);
    return Number.isFinite(d.getTime()) ? d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : String(value);
}
export const counts = (s) => [['Videos', s?.videos], ['Performers', s?.performers], ['Studios', s?.studios]].map(([l, v]) => [String(l), v === undefined ? '—' : num(v).toLocaleString()]);

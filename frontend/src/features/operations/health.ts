/** Library health check: local previews, artwork caches and stalled jobs (read-only; only artwork repair changes anything, and only locally). */
export interface Finding { code?: string; severity?: string; count?: number; message?: string }
export interface HealthReport { checked_at?: string; summary?: Record<string, unknown>; findings?: Finding[]; detail?: string }
export interface Summary { videos?: number; performers?: number; studios?: number; detail?: string }
export const LAST_KEY = 'ax-library-health-last-checked'
export const HEALTH_URL = '/api/operations/library-health?limit=100&stale_job_hours=24'
export const REPAIR_URL = '/api/operations/library-health/repair-artwork'
export const NOT_CHECKED = 'Run a check to review local previews, artwork caches and background jobs.'
export const NOT_CHECKED_DETAIL = 'Not checked. Scan for missing snapshots, empty artwork caches, and stale jobs.'
export const CHECKING = 'Checking local previews, artwork caches and background jobs…'
export const CHECK_FAILED_OVERVIEW = 'The check could not finish. Open the details for the error and try again.'
export const REPAIRING = 'Repairing artwork cache from matching local files only…'
export const num = (v: unknown) => Number(v || 0)

export type HealthState = 'unchecked' | 'checking' | 'healthy' | 'review' | 'error'
export const findingCount = (r: HealthReport) => num(r.summary?.finding_count ?? (r.findings ?? []).length)
export const stateOf = (r: HealthReport): HealthState => (findingCount(r) === 0 ? 'healthy' : 'review')
export const chipText = (s: HealthState, r?: HealthReport) => s === 'unchecked' ? 'Not checked' : s === 'checking' ? 'Checking…' : s === 'error' ? 'Check failed' : s === 'healthy' ? 'Healthy' : `${(r?.findings?.length || findingCount(r ?? {}) || 1)} finding${(r?.findings?.length || findingCount(r ?? {}) || 1) === 1 ? '' : 's'}`
export const overviewText = (s: HealthState) => s === 'healthy' ? 'Metadata, previews and background jobs look good. No issues were found.' : s === 'review' ? 'Review the reported findings below to repair missing previews, artwork or stalled jobs.' : s === 'error' ? CHECK_FAILED_OVERVIEW : s === 'checking' ? CHECKING : NOT_CHECKED
/** The plain-text report shown under "View check details". */
export function reportLines(r: HealthReport): string[] {
  const s = r.summary ?? {}
  const artwork = Object.values((s.artwork_cache_missing_bytes ?? {}) as Record<string, unknown>).reduce<number>((a, b) => a + num(b), 0)
  return [
    `Checked ${r.checked_at || 'now'}.`, `${num(s.active_media)} active media item(s).`, `${num(s.missing_snapshot_cache)} missing representative snapshot(s).`, `${num(s.empty_snapshot_blobs)} empty snapshot-cache row(s).`,
    `${num(s.missing_preview_files)} incomplete preview cache(s).`, `${num(s.stale_jobs)} stale background job(s).`, `Artwork cache misses: ${artwork}.`,
    ...(r.findings ?? []).map((f) => `${f.severity || 'info'} · ${f.code}: ${f.count} — ${f.message}`),
  ]
}
export const failedText = (why: string) => `Library health check failed: ${why}`
export const repairToast = (r: { repaired?: number; skipped?: number; failed?: number }) => `Artwork cache: ${r.repaired || 0} repaired, ${r.skipped || 0} unavailable locally, ${r.failed || 0} errors.`
export const REPAIR_FAILED = 'Artwork cache repair failed.'
export const repairFailedText = (why: string) => `Artwork cache repair failed: ${why}`
export function lastChecked(value: string | null | undefined): string {
  if (!value) return 'Never'
  const d = new Date(value)
  return Number.isFinite(d.getTime()) ? d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : String(value)
}
export const counts = (s: Summary | undefined): Array<[string, string]> => [['Videos', s?.videos], ['Performers', s?.performers], ['Studios', s?.studios]].map(([l, v]) => [String(l), v === undefined ? '—' : num(v).toLocaleString()])

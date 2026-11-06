/** Storage & Backup rules, ported from the classic UI (cache policy, data-folder move, backups, restore). */
type Obj = Record<string, unknown>
const s = (v: unknown) => String(v ?? '')
const n = (v: unknown) => (Number.isFinite(Number(v)) ? Number(v) : 0)

export const BUDGETS = [10, 25, 50, 100, 200, 500] as const
export const RETENTIONS = [['manual', 'Keep playback cache until I clear it'], ['idle_10m', 'Clear playback cache after 10 minutes idle'], ['idle_30m', 'Clear playback cache after 30 minutes idle (default)']] as const
export const READAHEADS = [[60, '60 seconds'], [120, '2 minutes'], [180, '3 minutes (default)'], [300, '5 minutes'], [600, '10 minutes']] as const
export const PROFILES = [['gentle', 'Gentle (1 stream connection)'], ['balanced', 'Balanced (3 stream connections, default)'], ['fast', 'Fast (5 stream connections)']] as const
export const RAMS = [[64, '64 MB'], [128, '128 MB (default)'], [256, '256 MB'], [512, '512 MB'], [1024, '1 GB']] as const

export interface CachePolicy { budget: number; retention: string; readahead: number; ram: number; profile: string }
export const DEFAULT_POLICY: CachePolicy = { budget: 50, retention: 'idle_30m', readahead: 180, ram: 128, profile: 'balanced' }
/** Server usage reply → the form (unknown values fall back to the classic defaults). */
export function policyFrom(r: Obj | undefined): CachePolicy {
  if (!r) return DEFAULT_POLICY
  return { budget: n(r.range_cache_budget_gib) || 50, retention: s(r.range_cache_retention) || 'idle_30m', readahead: n(r.playback_readahead_seconds) || 180, ram: n(r.playback_ram_cache_mb) || 128, profile: s(r.playback_connection_profile) || 'balanced' }
}
/** Whole GiB from 1 to 500, same as the classic check. */
export const policyProblem = (p: CachePolicy) => (Number.isInteger(p.budget) && p.budget >= 1 && p.budget <= 500 ? '' : 'Choose a whole-number limit between 1 and 500 GiB.')
export const policyBody = (p: CachePolicy) => ({ budget_gib: p.budget, retention: p.retention || 'idle_30m', readahead_seconds: p.readahead || 180, ram_mb: p.ram || 128, connection_profile: p.profile || 'balanced' })
export function retentionNote(retention: string): string {
  return retention === 'manual' ? 'Playback and prefetch ranges are kept until you clear them, except when the size limit requires inactive ranges to be evicted.'
    : retention === 'idle_10m' ? 'Playback byte caches expire after 10 minutes idle; speculative prefetch also expires after 10 minutes.'
    : 'Playback byte caches expire after 30 minutes idle; speculative prefetch expires after 10 minutes.'
}
export function savedMessage(r: Obj, fmt: (b: number) => string): string {
  const policy = r.retention === 'manual' ? 'playback cache kept until manually cleared' : r.retention === 'idle_10m' ? 'playback cache cleared after 10 minutes idle' : 'playback cache cleared after 30 minutes idle'
  const evicted = n(r.evicted_bytes)
  return `Saved · ${s(r.budget_gib)} GiB · ${policy}${evicted ? ` · removed ${fmt(evicted)} of inactive cache` : ''}`
}
export function usageCards(r: Obj, fmt: (b: number) => string): Array<{ label: string; value: string }> {
  return [
    { label: 'Persistent video byte cache', value: `${fmt(n(r.range_cache_bytes))} of ${fmt(n(r.range_cache_budget_bytes))}` },
    { label: 'Cached videos', value: `${n(r.range_cache_entries)} entries · ${n(r.range_cache_complete)} complete` },
    { label: 'Kept buffers', value: `${n(r.kept_entries)} video${n(r.kept_entries) === 1 ? '' : 's'} · ${fmt(n(r.kept_bytes))}` },
    { label: 'Snapshots', value: fmt(n(r.snapshots_bytes)) },
    { label: 'MP4 index cache', value: fmt(n(r.mp4_index_bytes)) },
    { label: 'Keyframe index data', value: fmt(n(r.keyframe_index_bytes)) },
  ]
}

export interface StorageInfo { current_path?: string; pending_path?: string; environment_override?: boolean; blocking_jobs?: number; files?: number; bytes?: number; disk?: { total?: number; free?: number } }
/** The warning above the move form: environment wins, then running jobs, then a staged move. */
export function moveWarning(r: StorageInfo): string {
  if (r.environment_override) return 'ARCHIVE_X_DATA_DIR (or the legacy TELEGRAM_ARCHIVE_DATA_DIR) is set in the environment, so the environment path takes precedence over this setting.'
  if (n(r.blocking_jobs)) return `${n(r.blocking_jobs)} running/queued job(s) must be paused or stopped before moving the data folder. Paused jobs can remain paused.`
  if (r.pending_path) return 'The move is staged safely. On the next clean startup Archive X will copy the complete data tree, verify the copy, then remove the old folder.'
  return ''
}
export const moveBlocked = (r: StorageInfo) => Boolean(r.environment_override) || n(r.blocking_jobs) > 0
export const moveProblem = (dest: string) => (dest.trim() ? '' : 'Select a destination folder first.')
export const moveConfirmText = (dest: string) => `Move all Archive X app data to:\n\n${dest}\n\nThe current data will be copied and verified on the next restart, then the old folder will be removed. Continue?`

export interface BackupSettings { enabled: boolean; repository: string; daily: number; weekly: number; monthly: number; time: string }
export function backupFrom(r: Obj | undefined): BackupSettings {
  const b = (r?.settings ?? {}) as Obj
  return { enabled: Boolean(b.enabled), repository: s(b.repository), daily: b.retention_daily == null ? 7 : n(b.retention_daily), weekly: b.retention_weekly == null ? 4 : n(b.retention_weekly), monthly: b.retention_monthly == null ? 3 : n(b.retention_monthly), time: s(b.schedule_time) || '02:00' }
}
export const backupSettingsBody = (b: BackupSettings) => ({ enabled: b.enabled, repository: b.repository, daily: b.daily, weekly: b.weekly, monthly: b.monthly, schedule_time: b.time || '02:00' })
export function backupStatusLine(r: Obj | undefined): string {
  const b = (r?.settings ?? {}) as Obj
  return b.last_error ? `Last backup error: ${s(b.last_error)}` : b.last_backup ? `Last verified backup: ${s(b.last_backup)}` : 'No verified backup has been created yet.'
}
export const backupPasswordProblem = (pw: string) => (pw && pw.length < 8 ? 'Backup password must be at least 8 characters.' : '')
export const BACKUP_TYPES = [['full_application', 'Full application'], ['configuration', 'Configuration + database']] as const
export interface BackupRow { name: string; path: string; created: string; size: number; files: number; verified: boolean }
export function backupRows(r: Obj | undefined): BackupRow[] {
  return ((Array.isArray(r?.backups) ? r!.backups : []) as Obj[]).map((x) => ({ name: s(x.name), path: s(x.path), created: s(x.created_at), size: n(x.size), files: n(x.files), verified: Boolean(x.verified) }))
}
export function restorePreview(r: Obj): string {
  const m = (r.manifest ?? {}) as Obj
  const files = Array.isArray(m.files) ? m.files.length : 0
  return `Valid backup · ${s(m.application_version)} · ${files.toLocaleString()} files · ${m.encrypted ? 'encrypted' : 'not encrypted'} · restart required. ${r.active_jobs ? 'Stop active jobs before restore.' : 'Ready to stage.'}`
}

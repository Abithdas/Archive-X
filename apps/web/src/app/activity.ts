import { useStatus } from '@/api/hooks'

/** Which sidebar page each kind of running work belongs to. Operations shows all of it, so it also counts everything. */
const PAGE_OF: Record<string, string> = {
  TELEGRAM_TRANSFER: '/transfers',
  MEDIA_MANAGER: '/transfers',
  UPLOAD_LOCAL: '/transfers',
  CONTENT_AUTOMATION: '/transfers',
  METADATA_SYNC: '/videos',
  SUGGESTION_SCAN: '/videos',
  METADATA_ASSETS: '/videos',
  METADATA_CHANNEL_PIPELINE: '/videos',
  THUMBNAIL_INDEXER: '/videos',
  ASSET_PREFETCH: '/videos',
  PREVIEW_PREFETCH: '/videos',
  PLAYBACK: '/videos',
  ENTITY_IMAGE_SYNC: '/performers',
  BACKUP_CREATE: '/settings',
}
/** Statuses that mean work is happening now (a paused, queued or interrupted job is not activity). */
const WORKING = new Set(['RUNNING', 'PAUSING', 'CANCEL_REQUESTED', 'RECOVERING'])

interface JobLike { type?: unknown; status?: unknown }

/**
 * How many jobs are running for each sidebar page, keyed by its path. Pages with nothing running are left out.
 * Entity data (performer and channel pictures and details) belongs to both the Performers and the Studios page.
 * Playback is shown on Videos only while a video is playing, and is not counted under Operations (it is not a job you manage there).
 */
export function activityByPage(jobs: unknown): Record<string, number> {
  const out: Record<string, number> = {}
  const add = (to: string) => { out[to] = (out[to] ?? 0) + 1 }
  if (!Array.isArray(jobs)) return out
  for (const j of jobs as JobLike[]) {
    if (!j || typeof j !== 'object') continue
    const type = String(j.type ?? '').toUpperCase()
    if (!WORKING.has(String(j.status ?? '').toUpperCase()) && type !== 'PLAYBACK') continue
    const page = PAGE_OF[type]
    if (page) add(page)
    if (type === 'ENTITY_IMAGE_SYNC') add('/studios')
    if (type !== 'PLAYBACK') add('/operations')
  }
  return out
}

export function useActivity(): Record<string, number> {
  const { data } = useStatus()
  return activityByPage(data?.jobs)
}

/** "3 running" for screen readers and tooltips. */
export const runningLabel = (n: number) => `${n} running`

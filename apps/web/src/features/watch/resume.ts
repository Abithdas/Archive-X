/**
 * Resume rules, ported from the classic player. The localStorage keys are the same as the classic UI's, so both UIs
 * share one saved position. Only the last-played video keeps a position (the classic behaviour).
 */
import { formatDuration } from '@/features/library/format'

export const LAST_KEY = 'ta_last_played_video'
export const SAVE_EVERY_MS = 5000
export const vkey = (cid: string, mid: number) => `${cid}:${mid}`
export const resumeStorageKey = (cid: string, mid: number) => `ta_resume:${vkey(cid, mid)}`

/** Keep a position locally only while the video is the last one played and clearly in the middle (not the first 20 s, not the last 25 s). */
export const shouldSaveLocal = (isLast: boolean, position: number, duration: number) => isLast && position > 20 && duration > 0 && position < duration - 25
export const dueForSave = (now: number, last: number, every = SAVE_EVERY_MS) => now - last >= every

/** The saved position to offer, or null. Needs the setting on, a saved video that was last played, and at least 30 s but not the last 20 s. */
export function promptPosition(i: { enabled: boolean; fromQueue?: boolean; isLast: boolean; stored: number; duration: number }): number | null {
  if (!i.enabled || i.fromQueue || !i.isLast) return null
  const ok = Number.isFinite(i.stored) && i.stored >= 30 && (!i.duration || i.stored < i.duration - 20)
  return ok ? i.stored : null
}

/** Watch time only counts for ordinary playback: a jump of more than 15 s (a seek) adds nothing. */
export const progressBody = (cid: string, mid: number, position: number, duration: number, previous: number) => {
  const delta = Math.max(0, position - previous)
  return { channel_id: cid, message_id: mid, position, duration, watch_delta: delta <= 15 ? delta : 0 }
}
export const finishedBody = (cid: string, mid: number, duration: number) => ({ channel_id: cid, message_id: mid, position: duration, duration, watch_delta: 0 })

export const labelFor = (seconds: number) => formatDuration(seconds) || `${Math.floor(seconds)}s`
export const progressPct = (stored: number, duration: number) => (duration > 0 ? Math.max(0, Math.min(100, (stored / duration) * 100)) : 0)
export const timeLine = (label: string, duration: number) => `${label}${duration > 0 ? ` / ${formatDuration(duration) || ''}` : ''}`

export type Choice = 'resume' | 'start' | 'cancel'
/** What the player does after the choice: where to start, and whether to play. Cancelling leaves the video paused at the start. */
export const outcome = (choice: Choice, stored: number) => ({ startAt: choice === 'resume' ? Math.max(0, stored) : 0, autoPlay: choice !== 'cancel' })

// ── storage (every access guarded: private mode and blocked storage must never break playback) ──
const get = (k: string) => { try { return localStorage.getItem(k) } catch { return null } }
const set = (k: string, v: string) => { try { localStorage.setItem(k, v) } catch { /* ignore */ } }
const del = (k: string) => { try { localStorage.removeItem(k) } catch { /* ignore */ } }
export const isLastPlayed = (cid: string, mid: number) => get(LAST_KEY) === vkey(cid, mid)
export const markPlayed = (cid: string, mid: number) => set(LAST_KEY, vkey(cid, mid))
export const readStored = (cid: string, mid: number) => Number(get(resumeStorageKey(cid, mid)) || 0)
export const saveStored = (cid: string, mid: number, position: number) => set(resumeStorageKey(cid, mid), String(Math.floor(position)))
export const clearStored = (cid: string, mid: number) => { del(resumeStorageKey(cid, mid)); if (isLastPlayed(cid, mid)) del(LAST_KEY) }

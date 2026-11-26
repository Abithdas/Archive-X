import type { QueryClient } from '@tanstack/react-query'
import type { Mode } from './content'

/**
 * Switching SFW <-> NSFW without the wait and without the half-and-half screen.
 *
 * What is on screen belongs to the old mode, and used to stay there, then be replaced piece by piece as each request finished (five steps over a
 * second when switching to NSFW, and adult cards still showing for a moment after switching to SFW). Now:
 *  - the data of the mode being left is remembered, and the data last seen in the mode being entered is put back at once, so a switch back is
 *    immediate; it is refreshed behind the screen, and only changes what really changed;
 *  - a page that has nothing remembered for the new mode loses the old mode's data at once (a skeleton, never the wrong content);
 *  - only what depends on the mode is touched: the profile, the settings and the like are not fetched again.
 */
const MODE_FREE = new Set(['content-mode', 'settings', 'profile', 'login-hint', 'storage', 'diagnostics', 'ai-models', 'ai-status', 'captions-model'])
const MAX_AGE_MS = 10 * 60_000   // older than this, a remembered screen is not worth showing first

type Remembered = { at: number; data: Map<string, unknown> }
const remembered = new Map<Mode, Remembered>()
const keyOf = (key: readonly unknown[]) => JSON.stringify(key)
const dependsOnMode = (key: readonly unknown[]) => !MODE_FREE.has(String(key[0]))

/** Forget everything remembered (signing out, locking): the data of the other mode must not outlive the session. */
export function forgetModeData() {
  remembered.clear()
}

export function swapModeData(qc: QueryClient, from: Mode, to: Mode, now: number = Date.now()) {
  if (from === to) return
  const cache = qc.getQueryCache()
  const queries = cache.getAll().filter((q) => dependsOnMode(q.queryKey))
  const mine = new Map<string, unknown>()
  for (const q of queries) if (q.state.data !== undefined) mine.set(keyOf(q.queryKey), q.state.data)
  remembered.set(from, { at: now, data: mine })

  const theirs = remembered.get(to)
  const usable = theirs && now - theirs.at <= MAX_AGE_MS ? theirs.data : undefined
  for (const q of queries) {
    const saved = usable?.get(keyOf(q.queryKey))
    if (saved !== undefined) {
      qc.setQueryData(q.queryKey, saved)                                   // what was last seen in this mode, on screen at once
      void qc.invalidateQueries({ queryKey: q.queryKey, exact: true })     // and made current behind it
    } else {
      void qc.resetQueries({ queryKey: q.queryKey, exact: true })          // nothing to show: no data of the other mode, fetched fresh
    }
  }
}

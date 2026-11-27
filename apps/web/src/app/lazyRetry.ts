/* eslint-disable @typescript-eslint/no-explicit-any */
import { lazy, type ComponentType } from 'react'

const KEY = 'ax-chunk-reload'
const WINDOW_MS = 30_000

/** A page file that could not be fetched (an old tab after an update, or the app restarting at that moment): the failed import is remembered by the browser, so retrying in place cannot work. */
export const isChunkFailure = (e: unknown) => /dynamically imported module|Importing a module script failed|Loading chunk|error loading dynamically/i.test(e instanceof Error ? e.message : String(e))

/** True when a reload for this reason has not just been tried: one reload per half minute at most, so a file that is really gone cannot loop. */
export function mayReload(now = Date.now(), store: Pick<Storage, 'getItem' | 'setItem'> | null = typeof sessionStorage === 'undefined' ? null : sessionStorage): boolean {
  try {
    const last = Number(store?.getItem(KEY) ?? 0)
    if (Number.isFinite(last) && now - last < WINDOW_MS) return false
    store?.setItem(KEY, String(now))
    return true
  } catch { return true }
}

/** `lazy()` for a page: when its file cannot be fetched the page reloads itself once (the new copy has the right file names) instead of showing an error. */
export function lazyPage<T extends ComponentType<any>>(load: () => Promise<{ default: T }>, reload: () => void = () => window.location.reload()) {
  return lazy(async () => {
    try { return await load() } catch (e) {
      if (isChunkFailure(e) && mayReload()) { reload(); return new Promise<never>(() => undefined) }   // the page is going away: never settle
      throw e
    }
  })
}

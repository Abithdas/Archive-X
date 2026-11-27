import { useSyncExternalStore } from 'react'

/**
 * Full screen for the video page. The browser's full screen belongs to the whole page (not to the player's own element), because the player is taken off
 * the page and put back whenever another video is chosen (Next, the end of a video, Up next): a full screen on the player itself ends the moment it
 * is removed, and the new video would start in a window. The page stays in full screen and each player shows itself over it (see Player: top layer).
 */
let want = false
const subs = new Set<() => void>()
const emit = () => subs.forEach((f) => f())
const root = (): HTMLElement | null => (typeof document === 'undefined' ? null : document.documentElement)
const active = () => want && typeof document !== 'undefined' && document.fullscreenElement === root()

/** The page is in full screen because the person asked the video page for it. */
export const fullscreenOn = active

export function enterFullscreen(): void {
  want = true
  emit()
  const r = root()
  void Promise.resolve(r?.requestFullscreen?.()).catch(() => { want = false; emit() })
}

export function leaveFullscreen(): void {
  const was = want
  want = false
  if (was) emit()
  if (typeof document !== 'undefined' && document.fullscreenElement) void Promise.resolve(document.exitFullscreen?.()).catch(() => undefined)
}

if (typeof document !== 'undefined') {
  // Esc, or the system, ended full screen: it is over until it is asked for again.
  document.addEventListener('fullscreenchange', () => {
    if (want && document.fullscreenElement !== root()) want = false
    emit()
  })
}

const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f) } }
export const useFullscreen = (): boolean => useSyncExternalStore(subscribe, active, () => false)

// ---- a video chosen with Next / Previous / Up next / the end of the last one starts playing, whatever "autoplay" says --------------------------------
let playKey = ''
export const markPlayNext = (cid: string, mid: number | string): void => { playKey = `${cid}:${mid}` }
export const shouldPlayNow = (cid: string, mid: number | string): boolean => playKey === `${cid}:${mid}`

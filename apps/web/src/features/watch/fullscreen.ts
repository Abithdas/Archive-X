import { useEffect, useRef, useSyncExternalStore } from 'react'

/**
 * Full screen for the video page. The browser's full screen belongs to the whole page (not to the player's own element), because the player is taken off
 * the page and put back whenever another video is chosen (Next, the end of a video, Up next): a full screen on the player itself ends the moment it
 * is removed, and the new video would start in a window. The page stays in full screen and each player shows itself over it (see Player: top layer).
 */
let want = false
let auto = false      // the video page was opened by playInFullscreen (a click on Play), so there is no video page to stay on once full screen is left
let escaped = false   // full screen ended by Esc or the system, not by the player's own button
const subs = new Set<() => void>()
const emit = () => subs.forEach((f) => f())
const root = (): HTMLElement | null => (typeof document === 'undefined' ? null : document.documentElement)
const active = () => want && typeof document !== 'undefined' && document.fullscreenElement === root()

/** The page is in full screen because the person asked the video page for it. */
export const fullscreenOn = active

export function enterFullscreen(): void {
  want = true
  escaped = false
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
    if (want && document.fullscreenElement !== root()) { want = false; escaped = true }
    emit()
  })
}

const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f) } }
export const useFullscreen = (): boolean => useSyncExternalStore(subscribe, active, () => false)

// ---- a video chosen with Next / Previous / Up next / the end of the last one starts playing, whatever "autoplay" says --------------------------------
let playKey = ''
export const markPlayNext = (cid: string, mid: number | string): void => { playKey = `${cid}:${mid}` }
export const shouldPlayNow = (cid: string, mid: number | string): boolean => playKey === `${cid}:${mid}`

// ---- playing straight into full screen (SFW), like dx.bi: no video page to look at ----------------------------------------------------------------
/** Press Play: full screen is asked for in this very click (the browser only allows it then) and the video page opens already in it. Esc then takes the person back to where they were. */
export function playInFullscreen(): void {
  auto = true
  enterFullscreen()
}

/** The video could not be started after all (the click had already gone to full screen): back to the page, out of full screen. */
export function cancelFullscreenPlay(): void {
  auto = false
  leaveFullscreen()
}

/** For a Link to a video: a plain click plays in full screen; a click that means something else (new tab, new window, a download) is left alone. */
export const playClick = (e: { button: number; metaKey: boolean; ctrlKey: boolean; shiftKey: boolean; altKey: boolean }): void => {
  if (e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey) playInFullscreen()
}

/** On the video page: when it was opened by Play and full screen is left with Esc, go back to the page the person came from. Leaving with the player's own button keeps the video in its window. */
export function useBackOnEscape(back: () => void): void {
  const fs = useFullscreen()
  const opened = useRef(auto)           // set by Play just before this page was shown
  const seen = useRef(false)
  const go = useRef(back)
  go.current = back
  useEffect(() => {
    if (fs) { seen.current = true; return }
    if (seen.current && opened.current && escaped) { seen.current = false; opened.current = false; auto = false; escaped = false; go.current() }
  }, [fs])
  useEffect(() => () => { auto = false }, [])
}

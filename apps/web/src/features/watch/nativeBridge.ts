/** The desktop app's native video player (native/axplayer), reached through the Tauri commands in apps/desktop/src-tauri/src/native.rs.
 *  Only the installed app has them; in a browser `hasNativeBridge()` is false and nothing here is used. */

import { pageZoom } from '@/features/library/screenGrid'

type Invoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>

const invoke = (): Invoke | undefined => (window as unknown as { __TAURI_INTERNALS__?: { invoke?: Invoke } }).__TAURI_INTERNALS__?.invoke

export const hasNativeBridge = () => typeof invoke() === 'function'

/** Where the video window sits: in front of the page, behind it, or in its own window above the app. */
export type VideoLayer = 'front' | 'behind' | 'popup'
export interface Availability { available: boolean; reason: string }
export interface Opened { duration: number; width: number; height: number; decoder: string; layer: VideoLayer }
export interface Snapshot {
  position: number; duration: number; playing: boolean; ended: boolean; buffering: boolean; width: number; height: number; error: number; opened: boolean; message: string
  /** The video window: shown, its real size in pixels, and its layer. */
  windowVisible: boolean; windowWidth: number; windowHeight: number; layer: VideoLayer
  /** What the renderer says it drew and dropped (-1: unknown), and what is really on screen there: distinct colours (1 or 2 = flat) and brightness range. */
  framesDrawn: number; framesDropped: number; screenDistinct: number; screenLumaRange: number
}
export interface NativeTrack { name: string; selected: boolean }
/** Where the video goes, in device pixels of the app window. */
export interface PixelRect { x: number; y: number; w: number; h: number }

async function call<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  const inv = invoke()
  if (!inv) throw new Error('The native player is only in the desktop app')
  try {
    return (await inv(command, args)) as T
  } catch (e) {
    throw new Error(typeof e === 'string' ? e : e instanceof Error ? e.message : 'The native player failed')
  }
}

export type NativeCommand = 'play' | 'pause' | 'stop' | 'seek' | 'rate' | 'volume' | 'mute' | 'audio' | 'front' | 'back' | 'popup' | 'hide' | 'close'

export const native = {
  available: () => call<Availability>('native_available'),
  open: (url: string, rect: PixelRect, hw?: number) => call<Opened>('native_open', { url, ...rect, hw }),
  command: (command: NativeCommand, value?: number) => call<void>('native_command', { command, value }),
  rect: (rect: PixelRect) => call<void>('native_rect', { ...rect }),
  state: () => call<Snapshot>('native_state'),
  tracks: () => call<NativeTrack[]>('native_tracks'),
  diagnostics: () => call<string>('native_diagnostics'),
}

/**
 * How many device pixels one pixel of `el.getBoundingClientRect()` is. The app zooms the page (wide windows, and the Settings display size), and which
 * pixels a browser reports then depends on its version: newer ones report the zoomed ones (the rect is as big on screen as it says), older ones the
 * unzoomed ones. The element's own width settles it: if its rect is about `zoom` times its layout width the rect is already zoomed, if it is about the
 * same the rect is not and the zoom is applied here. With no zoom there is nothing to tell apart.
 */
export function deviceScale(el: Element, ratio: number = window.devicePixelRatio || 1, zoom: number = pageZoom()): number {
  const layout = (el as HTMLElement).offsetWidth
  if (!(Math.abs(zoom - 1) > 0.01) || !(layout > 0)) return ratio
  const seen = el.getBoundingClientRect().width / layout
  return Math.abs(seen - zoom) <= Math.abs(seen - 1) ? ratio : ratio * zoom
}

/** An element's place in device pixels. */
export function pixelRect(el: Element, ratio: number = window.devicePixelRatio || 1, zoom: number = pageZoom()): PixelRect {
  const r = el.getBoundingClientRect()
  const k = deviceScale(el, ratio, zoom)
  return { x: Math.round(r.left * k), y: Math.round(r.top * k), w: Math.round(r.width * k), h: Math.round(r.height * k) }
}

/** The app's own update, kept by the desktop shell (apps/desktop/src-tauri/src/updates.rs). */
export type UpdatePhase = 'idle' | 'checking' | 'uptodate' | 'available' | 'downloading' | 'installing' | 'error'
export interface UpdateState {
  phase: UpdatePhase
  current: string
  version: string | null
  notes: string | null
  received: number
  total: number
  /** Whole percent downloaded; null while the size is not known. */
  percent: number | null
  error: string | null
  /** Unix seconds of the last finished look for an update. */
  checkedAt: number | null
}
export const updates = {
  status: () => call<UpdateState>('update_status'),
  check: () => call<UpdateState>('update_check'),
  install: () => call<UpdateState>('update_install'),
}

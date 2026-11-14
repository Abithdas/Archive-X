/** The desktop app's native video player (desktop/axplayer), reached through the Tauri commands in desktop/src-tauri/src/native.rs.
 *  Only the installed app has them; in a browser `hasNativeBridge()` is false and nothing here is used. */

type Invoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>

const invoke = (): Invoke | undefined => (window as unknown as { __TAURI_INTERNALS__?: { invoke?: Invoke } }).__TAURI_INTERNALS__?.invoke

export const hasNativeBridge = () => typeof invoke() === 'function'

export interface Availability { available: boolean; reason: string }
export interface Opened { duration: number; width: number; height: number; decoder: string; videoOnTop: boolean }
export interface Snapshot {
  position: number; duration: number; playing: boolean; ended: boolean; buffering: boolean; width: number; height: number; error: number; opened: boolean; message: string
  /** The video window: shown, its real size in pixels, and whether it is in front of the page. */
  windowVisible: boolean; windowWidth: number; windowHeight: number; videoOnTop: boolean
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

export type NativeCommand = 'play' | 'pause' | 'stop' | 'seek' | 'rate' | 'volume' | 'mute' | 'audio' | 'front' | 'back' | 'close'

export const native = {
  available: () => call<Availability>('native_available'),
  open: (url: string, rect: PixelRect, hw?: number) => call<Opened>('native_open', { url, ...rect, hw }),
  command: (command: NativeCommand, value?: number) => call<void>('native_command', { command, value }),
  rect: (rect: PixelRect) => call<void>('native_rect', { ...rect }),
  state: () => call<Snapshot>('native_state'),
  tracks: () => call<NativeTrack[]>('native_tracks'),
}

/** An element's place in device pixels. getBoundingClientRect is in the page's own pixels, zoom included (the app zooms wide windows), so only
 *  the screen's pixel ratio is left to apply. */
export function pixelRect(el: Element, ratio: number = window.devicePixelRatio || 1): PixelRect {
  const r = el.getBoundingClientRect()
  return { x: Math.round(r.left * ratio), y: Math.round(r.top * ratio), w: Math.round(r.width * ratio), h: Math.round(r.height * ratio) }
}

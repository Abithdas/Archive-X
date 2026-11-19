import { useEffect, useState } from 'react'

/** The desktop app's own window buttons (apps/desktop/src-tauri/src/window.rs). Only the installed app has them; in a browser `hasWindowChrome()` is false and the bar shows no buttons. */
type Invoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>
const invoke = (): Invoke | undefined => (window as unknown as { __TAURI_INTERNALS__?: { invoke?: Invoke } }).__TAURI_INTERNALS__?.invoke
export const hasWindowChrome = (): boolean => typeof invoke() === 'function'

const call = async <T = void>(command: string, args?: Record<string, unknown>): Promise<T | undefined> => {
  const inv = invoke()
  if (!inv) return undefined
  try {
    return (await inv(command, args)) as T
  } catch {
    return undefined   // a window command that fails leaves the window as it is
  }
}

export const windowActions = {
  minimize: () => call('window_minimize'),
  toggleMaximize: () => call<boolean>('window_toggle_maximize'),
  close: () => call('window_close'),
  drag: () => call('window_drag'),
}

/** While `on` is true (and this is the desktop app) the native title bar is hidden, because the page draws the window's buttons. Returns whether it is, and whether the window is maximised. */
export function useCustomChrome(on: boolean): { custom: boolean; maximized: boolean } {
  const bridge = hasWindowChrome()
  const custom = bridge && on
  const [maximized, setMaximized] = useState(false)
  useEffect(() => {
    if (!bridge) return
    void call('window_chrome', { custom: on })
    if (!on) return
    const ask = () => void call<boolean>('window_is_maximized').then((m) => setMaximized(Boolean(m)))
    ask()
    window.addEventListener('resize', ask)
    return () => window.removeEventListener('resize', ask)
  }, [bridge, on])
  return { custom, maximized }
}

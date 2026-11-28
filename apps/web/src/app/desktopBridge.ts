/** The desktop app's app-update commands (apps/desktop/src-tauri/src/updates.rs), reached through the Tauri bridge the shell puts on the page.
 *  Only the installed app has the bridge; in a browser `hasDesktopShell()` is false and nothing here is used. */

type Invoke = (command: string, args?: Record<string, unknown>) => Promise<unknown>

const invoke = (): Invoke | undefined => (window as unknown as { __TAURI_INTERNALS__?: { invoke?: Invoke } }).__TAURI_INTERNALS__?.invoke

export const hasDesktopShell = () => typeof invoke() === 'function'

async function call<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  const inv = invoke()
  if (!inv) throw new Error('This is only in the desktop app')
  try {
    return (await inv(command, args)) as T
  } catch (e) {
    throw new Error(typeof e === 'string' ? e : e instanceof Error ? e.message : 'The desktop app did not answer')
  }
}

/** The app's own update, kept by the desktop shell. */
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

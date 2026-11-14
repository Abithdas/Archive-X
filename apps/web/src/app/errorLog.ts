/** The last few uncaught errors, kept in memory so a problem report can include what led up to a crash. */
export interface LoggedError { at: string; message: string }
const MAX = 20
const log: LoggedError[] = []
const text = (v: unknown) => (v instanceof Error ? v.message : typeof v === 'string' ? v : (() => { try { return JSON.stringify(v) } catch { return String(v) } })()) ?? ''
export function record(v: unknown, now = new Date()) {
  log.push({ at: now.toISOString(), message: text(v).slice(0, 500) })
  if (log.length > MAX) log.shift()
}
export const recentErrors = (): readonly LoggedError[] => [...log]
export const clearErrors = () => { log.length = 0 }
let installed = false
export function installErrorLog() {
  if (installed || typeof window === 'undefined') return
  installed = true
  window.addEventListener('error', (e) => record(e.error ?? e.message))
  window.addEventListener('unhandledrejection', (e) => record(e.reason))
}

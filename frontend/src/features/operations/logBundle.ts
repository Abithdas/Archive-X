import { ApiError, api } from '@/api/client'
import { hasNativeBridge } from '@/features/watch/nativeBridge'

export const LOG_BUNDLE_URL = '/api/diagnostics/logs.zip'

export type LogBundleResult =
  | { kind: 'saved'; name: string; path: string; files: number }   // the desktop app: written to the Downloads folder
  | { kind: 'downloaded'; name: string }                           // a browser: handed to its download

/** The folder part of a Windows or Unix path, for a sentence like "Saved to C:\\Users\\me\\Downloads". */
export const folderOf = (path: string) => path.replace(/[\\/][^\\/]*$/, '') || path

const fileNameFrom = (disposition: string | null) => /filename\*?=(?:UTF-8'')?"?([^";]+)/i.exec(disposition ?? '')?.[1] ?? 'archive-x-logs.zip'

/**
 * Makes the logs-and-diagnostics zip (named with the date and time by the server). In the desktop app the server saves it into the
 * Downloads folder and says where, because an app window cannot always show a save dialog; in a browser it is an ordinary download.
 */
export async function downloadLogBundle(): Promise<LogBundleResult> {
  if (hasNativeBridge()) {
    const r = await api<{ name: string; path: string; files: number }>(`${LOG_BUNDLE_URL}?save=1`)
    return { kind: 'saved', name: r.name, path: r.path, files: r.files }
  }
  const res = await fetch(LOG_BUNDLE_URL, { credentials: 'same-origin' })
  if (!res.ok) {
    let detail = ''
    try { detail = String(((await res.json()) as { detail?: unknown }).detail ?? '') } catch { /* not JSON */ }
    throw new ApiError(res.status, detail)
  }
  const name = fileNameFrom(res.headers.get('Content-Disposition'))
  const url = URL.createObjectURL(await res.blob())
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
  return { kind: 'downloaded', name }
}

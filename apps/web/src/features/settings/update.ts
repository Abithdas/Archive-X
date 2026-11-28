import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { formatBytes } from '@/design/format'
import { hasDesktopShell, updates, type UpdateState } from '@/app/desktopBridge'

const KEY = ['app-update'] as const
const busy = (s: UpdateState | undefined) => s?.phase === 'checking' || s?.phase === 'downloading' || s?.phase === 'installing'

/** The app update as the desktop shell sees it, refreshed while something is going on. Without the desktop shell (a browser) there is nothing to ask. */
export function useAppUpdate() {
  const qc = useQueryClient()
  const here = hasDesktopShell()
  const state = useQuery({ queryKey: KEY, queryFn: updates.status, enabled: here, retry: false, refetchInterval: (q) => (busy(q.state.data) ? 700 : 20_000) })
  const put = (s: UpdateState) => qc.setQueryData(KEY, s)
  const check = useMutation({ mutationFn: updates.check, onSuccess: put })
  const install = useMutation({ mutationFn: updates.install, onSuccess: put })
  return { here, state, check, install }
}

export type Tone = 'ok' | 'warn' | 'bad' | 'busy' | 'plain'
export interface UpdateView {
  title: string
  detail: string
  tone: Tone
  /** 0 to 100 while downloading or installing; null when there is no bar (or the size is not known yet: an unmoving bar). */
  pct: number | null
  bar: boolean
  actions: Array<'check' | 'install' | 'retry'>
}

/** "2 min ago", "just now", for the time of the last look. */
export function agoText(checkedAt: number | null, nowSecs: number): string {
  if (!checkedAt) return ''
  const d = Math.max(0, nowSecs - checkedAt)
  if (d < 60) return 'just now'
  if (d < 3600) return `${Math.floor(d / 60)} min ago`
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`
  return `${Math.floor(d / 86400)} d ago`
}

/** What the card says and offers for each state of the update. */
export function updateView(s: UpdateState, nowSecs = Math.floor(Date.now() / 1000)): UpdateView {
  const ago = agoText(s.checkedAt, nowSecs)
  switch (s.phase) {
    case 'checking':
      return { title: 'Looking for an update…', detail: `You have ${s.current}.`, tone: 'busy', pct: null, bar: false, actions: [] }
    case 'uptodate':
      return { title: 'Archive X is up to date', detail: `Version ${s.current}${ago ? ` · checked ${ago}` : ''}`, tone: 'ok', pct: null, bar: false, actions: ['check'] }
    case 'available':
      return { title: `Version ${s.version ?? ''} is ready`, detail: `You have ${s.current}. Your library, settings and Telegram login are kept; the app restarts when it is done.${s.notes ? `\n\n${s.notes.trim()}` : ''}`, tone: 'warn', pct: null, bar: false, actions: ['install', 'check'] }
    case 'downloading': {
      const known = s.percent !== null && s.total > 0
      return { title: `Downloading version ${s.version ?? ''}`, detail: known ? `${formatBytes(s.received)} of ${formatBytes(s.total)} · ${s.percent}%` : s.received ? `${formatBytes(s.received)} so far` : 'Starting the download…', tone: 'busy', pct: known ? s.percent : 0, bar: true, actions: [] }
    }
    case 'installing':
      return { title: 'Installing the update', detail: 'Archive X closes and starts again by itself. Please do not turn off the PC.', tone: 'busy', pct: 100, bar: true, actions: [] }
    case 'error':
      return { title: 'The update did not work', detail: s.error || 'Something went wrong.', tone: 'bad', pct: null, bar: false, actions: s.version ? ['retry', 'check'] : ['check'] }
    default:
      return { title: 'Not checked yet', detail: `Version ${s.current}. Archive X looks by itself every six hours.`, tone: 'plain', pct: null, bar: false, actions: ['check'] }
  }
}

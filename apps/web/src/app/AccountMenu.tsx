import { ContentSwitch } from '@/features/content/ContentSwitch'
import { Link } from '@tanstack/react-router'
import { useQueryClient } from '@tanstack/react-query'
import { useId, useRef, useState } from 'react'
import { toast } from 'sonner'
import { SESSION_LOST_EVENT, api, ApiError } from '@/api/client'
import { useDesktopInfo, useProfile, useStatus, useStorage } from '@/api/hooks'
import { displayName } from '@/features/profile/profile'
import { AccountAvatar, ProfileEditor } from '@/features/profile/ProfileEditor'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { install, useInstall } from './pwa'
import { ThemeToggle } from '@/design/ThemeToggle'
import { toggleTheme, useTheme } from './theme'
import { useUi } from './ui'

const ROW = 'flex h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-[15px] font-medium hover:bg-fg/5 focus-visible:bg-fg/5 focus-visible:outline-none'

/** The little dot on the avatar says whether Telegram is connected (not whether the live-updates socket is): green, amber while the first answer is awaited, red. */
export function telegramDot(known: boolean, connected: boolean): { tone: string; label: string } {
  if (!known) return { tone: 'bg-warn', label: 'Checking Telegram' }
  return connected ? { tone: 'bg-good', label: 'Telegram connected' } : { tone: 'bg-bad', label: 'Telegram not connected' }
}

/** Avatar button and its menu: Telegram status, theme, storage, command palette, settings, sign out, exit. */
export function AccountMenu({ live }: { live: string }) {
  const id = useId()
  const pop = useRef<HTMLDivElement>(null)
  const [exit, setExit] = useState<'ask' | 'stopping' | 'failed' | null>(null)
  const [err, setErr] = useState('')
  const qc = useQueryClient()
  const profile = useProfile()
  const status = useStatus()
  const storage = useStorage()
  const desktop = useDesktopInfo()
  const theme = useTheme()
  const name = displayName(profile.data) || 'A'
  const [editing, setEditing] = useState(false)
  const offer = useInstall((st) => st.offer)
  const tg = Boolean(status.data?.telegram_connected ?? status.data?.telegram?.connected)
  const dot = telegramDot(Boolean(status.data), tg)
  const connectedNow = tg
  const d = storage.data?.disk
  const pct = d?.total ? Math.round(((d.used || d.total - d.free) / d.total) * 100) : null
  const close = () => pop.current?.hidePopover?.()

  const signOut = async () => {
    close()
    try {
      await api('/logout', { method: 'POST', json: {} })
    } catch {
      /* the session may already be gone */
    }
    qc.clear()
    window.dispatchEvent(new Event(SESSION_LOST_EVENT))
  }
  const quit = async () => {
    setExit('stopping')
    try {
      const r = await api<{ ok?: boolean; detail?: string }>('/api/app/exit', { method: 'POST', json: {} })
      if (r?.ok !== true) throw new ApiError(0, r?.detail || 'Shutdown request failed')
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : 'Shutdown request failed')
      setExit('failed')
      toast.error('Could not stop Archive X')
    }
  }

  return (
    <>
      <button type="button" popoverTarget={id} aria-label="Account menu" aria-haspopup="menu" className="relative grid size-10 place-items-center rounded-full bg-surface-3 text-[15px] font-bold" title={`${dot.label} · live updates: ${live}`}>
        <AccountAvatar className="size-10 text-[15px]" />
        <i data-live={connectedNow ? 'true' : 'false'} className={cn('ax-live-dot absolute right-0 top-0 size-2.5 border-2 border-bg', dot.tone)} role="status" aria-label={dot.label} />
      </button>
      <div ref={pop} id={id} popover="auto" role="menu" aria-label="Account menu" className="t-menu fixed right-4 top-[60px] left-auto bottom-auto m-0 w-[280px] rounded-[18px] border border-line-2 bg-surface text-fg shadow-[0_18px_50px_rgb(0_0_0/0.5)]">
        <div className="t-acc-panel-inner p-2">
        <div className="px-3 pb-1.5 pt-2 text-xs text-muted">Signed in as <b className="text-fg">{name}</b></div>
        <div className="px-3 pb-2 pt-1"><div className="mb-1 text-xs font-semibold text-muted">Content</div><ContentSwitch compact onDone={close} /></div>
        <button type="button" role="menuitem" className={ROW} onClick={() => { close(); setEditing(true) }}>
          <Icon name="edit" className="text-muted" />Edit profile
        </button>
        <Link to="/settings" role="menuitem" onClick={close} className={ROW}>
          <Icon name="send" className="text-muted" />Telegram
          <span className={cn('ml-auto rounded-full px-2 py-0.5 text-[11px] font-bold', tg ? 'bg-good/15 text-good' : 'bg-warn/15 text-warn')}>{tg ? 'Connected' : 'Setup needed'}</span>
        </Link>
        <div className={cn(ROW, 'cursor-default')}>
          <Icon name={theme === 'dark' ? 'moon' : 'sun'} className="text-muted" />Dark theme
          <ThemeToggle theme={theme} onToggle={toggleTheme} variant="switch" className="ml-auto inline-grid" />
        </div>
        <Link to="/settings" role="menuitem" onClick={close} className={ROW}>
          <Icon name="database" className="text-muted" />Storage
          <span className="ml-auto text-xs font-normal text-muted">{pct === null ? '—' : `${pct}% used`}</span>
        </Link>
        <button type="button" role="menuitem" className={ROW} onClick={() => { close(); useUi.getState().setPalette(true) }}>
          <Icon name="search" className="text-muted" />Command palette
          <kbd className="ml-auto rounded-md border border-line-2 px-1.5 py-0.5 font-sans text-[11px] font-semibold text-muted">Ctrl K</kbd>
        </button>
        {offer && !desktop.data ? (
          <button type="button" role="menuitem" className={ROW} onClick={() => { close(); void install() }}>
            <Icon name="download" className="text-muted" />Install app
          </button>
        ) : null}
        <Link to="/settings" role="menuitem" onClick={close} className={ROW}>
          <Icon name="gear" className="text-muted" />Settings
        </Link>
        <div role="separator" className="my-1.5 h-px bg-line" />
        {desktop.data ? null : (
          <button type="button" role="menuitem" className={cn(ROW, 'text-[13px]')} onClick={() => void signOut()}>
            <Icon name="lock" className="text-muted" />Sign out
          </button>
        )}
        <button type="button" role="menuitem" className={cn(ROW, 'text-[13px] text-bad')} onClick={() => { close(); setExit('ask') }}>
          <Icon name="close" />Exit app
        </button>
      </div>
      </div>
      {editing ? <ProfileEditor onClose={() => setEditing(false)} /> : null}
      {exit ? (
        <Dialog open onClose={() => exit !== 'stopping' && setExit(null)} title={exit === 'ask' ? 'Exit Archive X?' : exit === 'stopping' ? 'Archive X is shutting down' : 'Could not stop Archive X'}>
          {exit === 'ask' ? (
            <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
              <p className="text-sm text-muted">Exit Archive X completely? This stops the local server and background workers. Running jobs are saved for recovery where supported and may need to be resumed when you launch Archive X again.</p>
              <div className="flex flex-wrap justify-end gap-2">
                <button type="button" onClick={() => setExit(null)} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Keep Archive X open</button>
                <button type="button" onClick={() => void quit()} className="h-11 rounded-xl bg-bad px-4 text-sm font-bold text-white">Exit app</button>
              </div>
            </div>
          ) : exit === 'stopping' ? (
            <p className="text-sm text-muted" role="status">The request was accepted. Waiting for the server and background workers to stop…</p>
          ) : (
            <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
              <p className="text-sm text-muted" role="alert">{err} No successful shutdown was confirmed. You can retry or return to Archive X.</p>
              <div className="flex flex-wrap justify-end gap-2">
                <button type="button" onClick={() => setExit(null)} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">Return</button>
                <button type="button" onClick={() => void quit()} className="h-11 rounded-xl bg-bad px-4 text-sm font-bold text-white">Retry</button>
              </div>
            </div>
          )}
        </Dialog>
      ) : null}
    </>
  )
}

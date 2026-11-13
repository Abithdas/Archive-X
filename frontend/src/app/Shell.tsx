import { fadeThrough, motion, spring } from '@/design/motion'
import { installGlow } from '@/design/glow'
import { Link, Outlet, useNavigate, useRouterState } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { Suspense, useEffect, useRef, useState } from 'react'
import { ErrorBoundary } from './ErrorScreen'
import { useStatus, useStorage } from '@/api/hooks'
import { useLiveStatus } from '@/api/live'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { formatBytes } from '@/design/format'
import { AccountMenu } from './AccountMenu'
import { CommandPalette } from './CommandPalette'
import { MoreSheet } from './MoreSheet'
import { NotificationsPanel, useNotices } from './NotificationsPanel'
import { useSearch } from './search'
import { toggleTheme } from './theme'
import { useUi } from './ui'
import { useAppearance, useAppearanceActions, useAppearanceSync } from '@/features/settings/useAppearance'
import { scaleForKey } from '@/theme/themes'

interface NavDef {
  to: string
  label: string
  icon: string
}
const GROUPS: { title: string; items: NavDef[] }[] = [
  { title: 'Library', items: [
    { to: '/photos', label: 'Photos', icon: 'images' },
    { to: '/videos', label: 'Videos', icon: 'library' },
    { to: '/performers', label: 'Performers', icon: 'user' },
    { to: '/studios', label: 'Studios', icon: 'studio' },
  ] },
  { title: 'Work', items: [
    { to: '/operations', label: 'Operations', icon: 'operations' },
    { to: '/transfers', label: 'Transfers', icon: 'transfer' },
  ] },
  { title: 'System', items: [
    { to: '/settings', label: 'Settings', icon: 'gear' },
    { to: '/ai', label: 'AI Assistance', icon: 'sparkle' },
  ] },
]
const PHONE_NAV = ['/photos', '/videos', '/performers', '/studios', '/transfers']
const RAIL_KEY = 'ax_next_sidebar_rail'

const SIDE_ITEM = 'relative flex h-11 w-full items-center rounded-xl text-[15px] font-medium text-muted transition-colors hover:bg-fg/5 hover:text-fg'
const SIDE_ACTIVE = '!text-fg [&_svg]:text-accent'

function SideItem({ n, rail }: { n: NavDef; rail: boolean }) {
  const layout = rail ? 'justify-center' : 'gap-3 px-3'
  return (
    <Link to={n.to} activeOptions={{ exact: n.to === '/' }} className={cn(SIDE_ITEM, layout)} title={rail ? n.label : undefined} activeProps={{ className: SIDE_ACTIVE }}>
      {({ isActive }) => (
        <>
          {isActive ? <motion.span layoutId="side-pill" transition={spring} aria-hidden="true" className="absolute inset-0 rounded-xl bg-accent/15" /> : null}
          <Icon name={n.icon} className="relative" />
          <span className={cn('relative', rail && 'sr-only')}>{n.label}</span>
        </>
      )}
    </Link>
  )
}

function StorageCard() {
  const { data, isError } = useStorage()
  const d = data?.disk
  const pct = d?.total ? Math.max(0, Math.min(100, Math.round(((d.used || d.total - d.free) / d.total) * 100))) : 0
  return (
    <div className="mt-auto rounded-[14px] border border-line bg-surface p-3.5 text-xs text-muted" title="Archive X app storage summary">
      <div className="flex justify-between font-semibold text-fg">
        <span>Storage</span>
        <span className="font-normal text-muted">{d?.total ? `${pct}% used` : '—'}</span>
      </div>
      <div className="my-2 h-1.5 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-label="Drive space used" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
        <span className="block h-full rounded-full bg-accent transition-[width] duration-500" style={{ width: `${pct}%` }} />
      </div>
      {data ? (
        <>
          <div>App data: {formatBytes(data.bytes)} · {data.files.toLocaleString()} files</div>
          <div>{d?.total ? `${formatBytes(d.free)} free of ${formatBytes(d.total)}` : 'Drive capacity unavailable'}</div>
        </>
      ) : (
        <div>{isError ? 'Storage unavailable' : 'Loading…'}</div>
      )}
    </div>
  )
}

function TelegramPill() {
  const { data } = useStatus()
  const ok = Boolean(data?.telegram_connected ?? data?.telegram?.connected)
  return (
    <Link to="/settings" className="hidden h-9 items-center gap-2 rounded-full bg-good/15 px-3 text-[13px] font-semibold text-good sm:inline-flex" title={data?.telegram_status ?? 'Telegram status'}>
      <Icon name="send" className="size-4" />
      <span className={cn('size-1.5 rounded-full', ok ? 'bg-good' : 'bg-bad')} aria-hidden="true" />
      {ok ? 'Connected' : 'Setup needed'}
    </Link>
  )
}

const ICON_BTN = 'grid size-10 place-items-center rounded-xl text-muted hover:bg-fg/5 hover:text-fg'

function Header({ live }: { live: string }) {
  const { q, set } = useSearch()
  const focusTick = useUi((s) => s.focusSearch)
  const path = useRouterState({ select: (s) => s.location.pathname })
  const input = useRef<HTMLInputElement>(null)
  const notices = useNotices().length
  const placeholder = path.startsWith('/performers') ? 'Search performer…' : path.startsWith('/studios') ? 'Search studio…' : 'Search videos, performers, studios…'
  useEffect(() => set(''), [path.split('/')[1], set])
  useEffect(() => {
    if (focusTick) input.current?.focus()
  }, [focusTick])
  return (
    <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-line bg-bg/85 px-4 backdrop-blur-md sm:px-10">
      <img src="/static/icons/archive-x.png" alt="" className="size-8 rounded-full md:hidden" />
      <label className="flex h-11 min-w-0 max-w-[407px] flex-1 items-center gap-2.5 rounded-[14px] border border-line bg-surface pl-3.5 pr-2.5 text-faint focus-within:border-accent/50">
        <Icon name="search" />
        <input
          ref={input}
          type="search"
          value={q}
          onChange={(e) => set(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              set('')
              input.current?.blur()
            }
          }}
          placeholder={placeholder}
          aria-label="Search"
          className="min-w-0 flex-1 bg-transparent text-base text-fg outline-none placeholder:text-faint [&::-webkit-search-cancel-button]:hidden"
        />
        <kbd className="hidden rounded-md border border-line-2 px-1.5 py-0.5 font-sans text-[11px] font-semibold sm:block">Ctrl K</kbd>
      </label>
      <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
        <TelegramPill />
        <button type="button" onClick={toggleTheme} aria-label="Toggle light and dark theme" title="Toggle theme" className={cn(ICON_BTN, 'hidden sm:grid')}>
          <Icon name="moon" className="[[data-theme=light]_&]:hidden" />
          <Icon name="sun" className="hidden [[data-theme=light]_&]:block" />
        </button>
        <button type="button" onClick={() => useUi.getState().setNotifications(true)} aria-label={notices ? `Notifications, ${notices} shown` : 'Notifications'} title="Notifications" className={cn(ICON_BTN, 'relative')}>
          <Icon name="clock" />
          {notices ? <span className="absolute right-1 top-1 grid min-w-4 place-items-center rounded-full bg-accent px-1 text-[10px] font-bold leading-4 text-accent-fg">{notices}</span> : null}
        </button>
        <AccountMenu live={live} />
      </div>
    </header>
  )
}

/** Shortcuts that work anywhere except while typing: Ctrl/Cmd+K palette, "/" search, "g" Videos. */
function useGlobalKeys() {
  const navigate = useNavigate()
  const { scale } = useAppearance()
  const { setScale } = useAppearanceActions()
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      const typing = !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))
      const z = typing ? null : scaleForKey(e, scale)
      if (z !== null) {
        e.preventDefault()
        void setScale(z).catch(() => undefined)
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        const ui = useUi.getState()
        ui.setPalette(!ui.palette)
      } else if (!typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (e.key === '/') {
          e.preventDefault()
          useUi.getState().requestSearchFocus()
        } else if (e.key.toLowerCase() === 'g') {
          void navigate({ to: '/videos' })
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navigate, scale, setScale])
}

export function Shell(): ReactNode {
  const fullPath = useRouterState({ select: (s) => s.location.pathname })
  const section = useRouterState({ select: (s) => s.location.pathname.split('/')[1] || 'home' })
  const live = useLiveStatus(true)
  useAppearanceSync()
  useEffect(() => installGlow(), [])
  useGlobalKeys()
  const [rail, setRail] = useState(() => {
    try {
      return localStorage.getItem(RAIL_KEY) === '1'
    } catch {
      return false
    }
  })
  const toggleRail = () => {
    setRail(!rail)
    try {
      localStorage.setItem(RAIL_KEY, rail ? '0' : '1')
    } catch {
      /* private mode */
    }
  }
  const phone = GROUPS.flatMap((g) => g.items).filter((n) => PHONE_NAV.includes(n.to))
  return (
    <div className="flex h-full flex-col md:flex-row">
      <aside className={cn('hidden shrink-0 flex-col gap-0.5 overflow-y-auto overflow-x-hidden border-r border-line pb-4 transition-[width] duration-200 md:flex', rail ? 'w-[84px] px-2.5' : 'w-[248px] px-3')} aria-label="Main">
        <div className={cn('flex h-16 shrink-0 items-center gap-2.5 text-lg font-extrabold', rail ? 'justify-center' : 'px-1.5')}>
          <img src="/static/icons/archive-x.png" alt="" className="size-9 rounded-full" />
          {rail ? null : 'Archive X'}
          {rail ? null : (
            <button type="button" onClick={toggleRail} aria-label="Collapse sidebar" aria-expanded title="Collapse sidebar" className="ml-auto grid size-7 place-items-center rounded-lg text-muted hover:bg-fg/5 hover:text-fg">
              <span aria-hidden="true">×</span>
            </button>
          )}
        </div>
        {rail ? (
          <button type="button" onClick={toggleRail} aria-label="Expand sidebar" title="Expand sidebar" className={cn(SIDE_ITEM, 'mb-1 justify-center')}>
            <Icon name="chevron-right" />
          </button>
        ) : null}
        {GROUPS.map((g, i) => (
          <div key={g.title} className={cn('grid gap-0.5', i > 0 && 'mt-3')}>
            {rail ? <div className="mx-2 my-1 h-px bg-line" /> : <div className="px-3 pb-1.5 pt-1 text-[11px] font-bold tracking-[0.08em] text-faint">{g.title}</div>}
            {g.items.map((n) => <SideItem key={n.to} n={n} rail={rail} />)}
          </div>
        ))}
        {rail ? null : <StorageCard />}
      </aside>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Header live={live} />
        <main className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          <motion.div key={section} {...fadeThrough} className="flex min-h-0 flex-1 flex-col"><ErrorBoundary resetKey={fullPath}><Suspense fallback={<div role="status" aria-label="Loading page" className="grid flex-1 place-items-center py-24"><span className="size-6 animate-spin rounded-full border-2 border-line border-t-accent" /></div>}><Outlet /></Suspense></ErrorBoundary></motion.div>
        </main>
      </div>
      <nav className="flex border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] md:hidden" aria-label="Main">
        {phone.map((n) =>
          (
            <Link key={n.to} to={n.to} activeOptions={{ exact: n.to === '/' }} className="relative flex min-h-[60px] flex-1 flex-col items-center justify-center gap-1 text-[10.5px] font-semibold text-muted" activeProps={{ className: '!text-accent' }}>
              {({ isActive }) => (
                <>
                  {isActive ? <motion.span layoutId="phone-nav-marker" transition={spring} aria-hidden="true" className="absolute inset-x-4 top-0 h-[3px] rounded-b-full bg-accent" /> : null}
                  <Icon name={n.icon} />
                  <span className="truncate">{n.label}</span>
                </>
              )}
            </Link>
          ),
        )}
        <button type="button" onClick={() => useUi.getState().setMore(true)} className="flex min-h-[60px] flex-1 flex-col items-center justify-center gap-1 text-[10.5px] font-semibold text-muted">
          <Icon name="grid" />
          <span>More</span>
        </button>
      </nav>
      <CommandPalette />
      <NotificationsPanel />
      <MoreSheet />
    </div>
  )
}

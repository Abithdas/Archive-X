import { Link, Outlet, useRouterState } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { useEffect, useRef } from 'react'
import { useProfile, useStatus, useStorage } from '@/api/hooks'
import { useLiveStatus } from '@/api/live'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { formatBytes } from '@/design/format'
import { useSearch } from './search'

interface NavDef {
  to: string
  label: string
  icon: string
  /** Classic-UI-only pages link out until they are migrated. */
  legacy?: string
}
const GROUPS: { title: string; items: NavDef[] }[] = [
  { title: 'Library', items: [
    { to: '/photos', label: 'Photos', icon: 'images', legacy: '/#photos' },
    { to: '/', label: 'Videos', icon: 'library' },
    { to: '/performers', label: 'Performers', icon: 'user' },
    { to: '/studios', label: 'Studios', icon: 'studio' },
  ] },
  { title: 'Work', items: [
    { to: '/operations', label: 'Operations', icon: 'operations' },
    { to: '/transfers', label: 'Transfers', icon: 'transfer' },
  ] },
  { title: 'System', items: [
    { to: '/settings', label: 'Settings', icon: 'gear' },
    { to: '/ai', label: 'AI Assistance', icon: 'sparkle', legacy: '/#ai' },
  ] },
]
const PHONE_NAV = ['/', '/performers', '/studios', '/operations', '/transfers']

function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'
  document.documentElement.dataset.theme = next
  try {
    localStorage.setItem('ax_theme_mode', next)
  } catch {
    /* private mode */
  }
}

const SIDE_ITEM = 'flex h-11 w-full items-center gap-3 rounded-xl px-3 text-[15px] font-medium text-muted transition-colors hover:bg-fg/5 hover:text-fg'
const SIDE_ACTIVE = '!bg-accent/15 !text-fg [&_svg]:text-accent'

function SideItem({ n }: { n: NavDef }) {
  if (n.legacy)
    return (
      <a href={n.legacy} className={SIDE_ITEM} title={`${n.label} (classic interface)`}>
        <Icon name={n.icon} />
        <span>{n.label}</span>
      </a>
    )
  return (
    <Link to={n.to} activeOptions={{ exact: n.to === '/' }} className={SIDE_ITEM} activeProps={{ className: SIDE_ACTIVE }}>
      <Icon name={n.icon} />
      <span>{n.label}</span>
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
    <Link
      to="/settings"
      className="hidden h-9 items-center gap-2 rounded-full bg-good/15 px-3 text-[13px] font-semibold text-good sm:inline-flex"
      title={data?.telegram_status ?? 'Telegram status'}
    >
      <Icon name="send" className="size-4" />
      <span className={cn('size-1.5 rounded-full', ok ? 'bg-good' : 'bg-bad')} aria-hidden="true" />
      {ok ? 'Connected' : 'Setup needed'}
    </Link>
  )
}

function Header({ live }: { live: string }) {
  const { q, set } = useSearch()
  const path = useRouterState({ select: (s) => s.location.pathname.replace(/^\/app-next/, '') || '/' })
  const profile = useProfile()
  const input = useRef<HTMLInputElement>(null)
  const placeholder = path.startsWith('/performers') ? 'Search performer…' : path.startsWith('/studios') ? 'Search studio…' : 'Search videos, performers, studios…'
  useEffect(() => set(''), [path.split('/')[1], set])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        input.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  const initial = (profile.data?.name || profile.data?.username || 'A').trim().charAt(0).toUpperCase()
  return (
    <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-line bg-bg/85 px-4 backdrop-blur-md sm:px-10">
      <div className="flex items-center gap-2 text-lg font-extrabold md:hidden">
        <img src="/static/icons/archive-x.png" alt="" className="size-8 rounded-full" />
      </div>
      <label className="flex h-11 min-w-0 max-w-[407px] flex-1 items-center gap-2.5 rounded-[14px] border border-line bg-surface pl-3.5 pr-2.5 text-faint focus-within:border-accent/50">
        <Icon name="search" />
        <input
          ref={input}
          type="search"
          value={q}
          onChange={(e) => set(e.target.value)}
          placeholder={placeholder}
          aria-label="Search"
          className="min-w-0 flex-1 bg-transparent text-base text-fg outline-none placeholder:text-faint [&::-webkit-search-cancel-button]:hidden"
        />
        <kbd className="hidden rounded-md border border-line-2 px-1.5 py-0.5 font-sans text-[11px] font-semibold sm:block">Ctrl K</kbd>
      </label>
      <div className="ml-auto flex items-center gap-2">
        <TelegramPill />
        <button type="button" onClick={toggleTheme} aria-label="Toggle light and dark theme" title="Toggle theme" className="grid size-10 place-items-center rounded-xl text-muted hover:bg-fg/5 hover:text-fg">
          <Icon name="moon" className="[[data-theme=light]_&]:hidden" />
          <Icon name="sun" className="hidden [[data-theme=light]_&]:block" />
        </button>
        <a href="/" title="Classic interface" aria-label="Open classic interface" className="hidden size-10 place-items-center rounded-xl text-muted hover:bg-fg/5 hover:text-fg sm:grid">
          <Icon name="clock" />
        </a>
        <span className="relative grid size-10 place-items-center rounded-full bg-surface-3 text-[15px] font-bold" title={`Live updates: ${live}`}>
          {initial}
          <i className={cn('absolute bottom-0 right-0 size-2.5 rounded-full border-2 border-bg', live === 'live' ? 'bg-good' : live === 'connecting' ? 'bg-warn' : 'bg-bad')} role="status" aria-label={live === 'live' ? 'Live' : live === 'connecting' ? 'Connecting' : 'Offline, retrying'} />
        </span>
      </div>
    </header>
  )
}

export function Shell(): ReactNode {
  const live = useLiveStatus(true)
  return (
    <div className="flex h-full flex-col md:flex-row">
      <aside className="hidden w-[248px] shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-line px-3 pb-4 md:flex" aria-label="Main">
        <div className="flex h-16 shrink-0 items-center gap-2.5 px-1.5 text-lg font-extrabold">
          <img src="/static/icons/archive-x.png" alt="" className="size-9 rounded-full" />
          Archive X
        </div>
        {GROUPS.map((g, i) => (
          <div key={g.title} className={cn('grid gap-0.5', i > 0 && 'mt-3')}>
            <div className="px-3 pb-1.5 pt-1 text-[11px] font-bold tracking-[0.08em] text-faint">{g.title}</div>
            {g.items.map((n) => <SideItem key={n.to} n={n} />)}
          </div>
        ))}
        <StorageCard />
      </aside>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Header live={live} />
        <main className="min-h-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
      <nav className="flex border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] md:hidden" aria-label="Main">
        {GROUPS.flatMap((g) => g.items).filter((n) => PHONE_NAV.includes(n.to)).map((n) => (
          <Link key={n.to} to={n.to} activeOptions={{ exact: n.to === '/' }} className="flex min-h-[60px] flex-1 flex-col items-center justify-center gap-1 text-[10.5px] font-semibold text-muted" activeProps={{ className: '!text-accent' }}>
            <Icon name={n.icon} />
            <span className="truncate">{n.label}</span>
          </Link>
        ))}
      </nav>
    </div>
  )
}

import { fadeThrough, motion, spring } from '@/design/motion'
import { installGlow } from '@/design/glow'
import { Link, Outlet, useNavigate, useRouterState } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { ErrorBoundary } from './ErrorScreen'
import { useEntities, useStatus, useStorage } from '@/api/hooks'
import { useLiveStatus } from '@/api/live'
import { Bell } from '@/design/Bell'
import { SearchField } from './SearchField'
import { suggest } from './searchSuggest'
import { ThemeToggle } from '@/design/ThemeToggle'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { formatBytes } from '@/design/format'
import { runningLabel, useActivity } from './activity'
import { AccountMenu } from './AccountMenu'
import { CommandPalette } from './CommandPalette'
import { MoreSheet } from './MoreSheet'
import { NotificationsPanel, useUnseenCount } from './NotificationsPanel'
import { useSearch } from './search'
import { toggleTheme, useTheme } from './theme'
import { useUi } from './ui'
import { useAppearance, useAppearanceActions, useAppearanceSync } from '@/features/settings/useAppearance'
import { scaleForKey } from '@/theme/themes'
import { useScrollMemory } from './scrollMemory'
import { ScrambleText } from '@/design/ScrambleText'
import { useLabels, type Labels } from '@/features/content/labels'
import { IconSwap } from '@/design/IconSwap'
import { SubtleNumber } from '@/design/SubtleNumber'

interface NavDef {
  to: string
  label: string
  icon: string
}
const groupsFor = (l: Labels): { title: string; items: NavDef[] }[] => [
  { title: 'Library', items: [
    { to: '/photos', label: 'Photos', icon: 'images' },
    { to: '/videos', label: l.videos, icon: 'library' },
    ...(l.series ? [{ to: '/series', label: l.series, icon: 'transfer' }] : []),
    { to: '/performers', label: l.performers, icon: 'user' },
    { to: '/studios', label: l.studios, icon: 'studio' },
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
const PHONE_NAV = ['/photos', '/videos', '/series', '/performers', '/studios', '/transfers']
const RAIL_KEY = 'ax_next_sidebar_rail'

const SIDE_ITEM = 'relative flex h-11 w-full items-center rounded-xl text-[15px] font-medium text-muted transition-colors hover:bg-fg/5 hover:text-fg'
const SIDE_ACTIVE = '!text-fg [&_svg]:text-accent'

/** How many jobs are running for this page: a small number that pulses while work is going on. */
function ActivityBadge({ count, className }: { count: number; className?: string }) {
  if (!count) return null
  return (
    <span role="status" aria-label={runningLabel(count)} title={runningLabel(count)} className={cn('grid h-[18px] min-w-[18px] animate-pulse place-items-center rounded-full bg-accent px-1 text-[11px] font-bold leading-none text-accent-fg', className)}>
      {count > 99 ? '99+' : <SubtleNumber value={count} />}
    </span>
  )
}

function SideItem({ n, rail, count }: { n: NavDef; rail: boolean; count: number }) {
  const layout = rail ? 'justify-center' : 'gap-3 px-3'
  return (
    <Link to={n.to} activeOptions={{ exact: n.to === '/' }} className={cn(SIDE_ITEM, layout)} title={rail ? (count ? `${n.label} · ${runningLabel(count)}` : n.label) : undefined} activeProps={{ className: SIDE_ACTIVE }}>
      {({ isActive }) => (
        <>
          {isActive ? <motion.span layoutId="side-pill" transition={spring} aria-hidden="true" className="absolute inset-0 rounded-xl bg-accent/15" /> : null}
          <Icon name={n.icon} className="relative" />
          <span className={cn('relative', rail && 'sr-only')}><ScrambleText text={n.label} /></span>
          <ActivityBadge count={count} className={rail ? 'absolute right-1.5 top-1.5' : 'ml-auto'} />
        </>
      )}
    </Link>
  )
}

/** Folds the sidebar to its icon rail and opens it again. One button, so the two icons trade places in the same spot (the Transitions.dev
 *  icon swap): the hamburger while the sidebar is minimized (press to open), the X while it is open (press to fold). */
function RailToggle({ rail, onToggle }: { rail: boolean; onToggle: () => void }) {
  const label = rail ? 'Expand sidebar' : 'Collapse sidebar'
  return (
    <button type="button" onClick={onToggle} aria-label={label} aria-expanded={!rail} title={label} className="ax-rail-toggle grid size-10 shrink-0 place-items-center rounded-xl text-muted hover:bg-fg/5 hover:text-fg">
      <IconSwap on={!rail} offIcon={<Icon name="menu" />} onIcon={<Icon name="x" />} />
    </button>
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
          <div>App data: {formatBytes(data.bytes)} · <SubtleNumber value={data.files} /> files</div>
          <div>{d?.total ? `${formatBytes(d.free)} free of ${formatBytes(d.total)}` : 'Drive capacity unavailable'}</div>
        </>
      ) : (
        <div>{isError ? 'Storage unavailable' : 'Loading…'}</div>
      )}
    </div>
  )
}

/** The app's version under the storage card, so it is always clear which release is running ("v30.3.2"; a development build says "dev"). */
export const versionLabel = (v: string | undefined): string => { const t = String(v ?? '').trim(); return !t ? '' : /^\d/.test(t) ? `v${t}` : t }
function VersionLine() {
  const { data } = useStatus()
  const label = versionLabel(data?.version)
  return label ? <p className="mt-2 text-center text-[11px] font-medium text-faint" title="Archive X version">Archive X {label}</p> : null
}

function TelegramPill() {
  const { data } = useStatus()
  const ok = Boolean(data?.telegram_connected ?? data?.telegram?.connected)
  return (
    <Link to="/settings" data-live={ok ? 'true' : 'false'} className={cn('ax-live-pill hidden h-9 items-center gap-2 rounded-full px-3 text-[13px] font-semibold sm:inline-flex', ok ? 'bg-good/15 text-good' : 'bg-bad/15 text-bad')} title={data?.telegram_status ?? 'Telegram status'}>
      <Icon name="send" className="size-4" />
      <span data-live={ok ? 'true' : 'false'} className={cn('ax-live-dot size-2', ok ? 'bg-good' : 'bg-bad')} aria-hidden="true" />
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
  const notices = useUnseenCount()
  const theme = useTheme()
  const navigate = useNavigate()
  const [loadLists, setLoadLists] = useState(false)   // the lists the suggestions come from are fetched the first time the box is used
  const performers = useEntities('performers', loadLists)
  const studios = useEntities('studios', loadLists)
  const suggestions = useMemo(() => suggest(performers.data ?? [], studios.data ?? [], q), [performers.data, studios.data, q])
  const placeholder = path.startsWith('/performers') ? 'Search performer…' : path.startsWith('/studios') ? 'Search studio…' : 'Search videos, performers, studios…'
  useEffect(() => set(''), [path.split('/')[1], set])
  useEffect(() => {
    if (focusTick) input.current?.focus()
  }, [focusTick])
  return (
    <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-line bg-bg/85 px-4 backdrop-blur-md sm:px-10">
      <img src="/static/icons/archive-x.png" alt="" className="size-8 rounded-full md:hidden" />
      <SearchField
        value={q}
        onChange={set}
        placeholder={placeholder}
        suggestions={suggestions}
        onFirstUse={() => setLoadLists(true)}
        onPick={(s) => {
          set('')
          void navigate(s.kind === 'performers' ? { to: '/performers/$id', params: { id: String(s.entityId) } } : { to: '/studios/$id', params: { id: String(s.entityId) } })
        }}
        inputRef={input}
      />
      <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
        <TelegramPill />
        <ThemeToggle theme={theme} onToggle={toggleTheme} className={cn(ICON_BTN, 'hidden sm:grid')} />
        <button type="button" onClick={() => useUi.getState().setNotifications(true)} aria-label={notices ? `Notifications, ${notices} new` : 'Notifications'} title="Notifications" className={cn(ICON_BTN, 'relative')}>
          <Bell count={notices} />
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
  const mainRef = useRef<HTMLElement>(null)
  useScrollMemory(mainRef)
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
  const labels = useLabels()
  const GROUPS = groupsFor(labels)
  const phone = GROUPS.flatMap((g) => g.items).filter((n) => PHONE_NAV.includes(n.to))
  const activity = useActivity()
  return (
    <div className="flex h-full flex-col md:flex-row">
      <aside className={cn('hidden shrink-0 flex-col gap-0.5 overflow-y-auto overflow-x-hidden border-r border-line pb-4 transition-[width] duration-200 md:flex', rail ? 'w-[84px] px-2.5' : 'w-[248px] px-3')} aria-label="Main">
        <div className={cn('flex shrink-0 items-center text-lg font-extrabold', rail ? 'h-[92px] flex-col justify-center gap-1' : 'h-16 gap-2.5 px-1.5')}>
          <RailToggle rail={rail} onToggle={toggleRail} />
          <img src="/static/icons/archive-x.png" alt="" className="size-9 rounded-full" />
          {rail ? null : 'Archive X'}
        </div>
        {GROUPS.map((g, i) => (
          <div key={g.title} className={cn('grid gap-0.5', i > 0 && 'mt-3')}>
            {rail ? <div className="mx-2 my-1 h-px bg-line" /> : <div className="px-3 pb-1.5 pt-1 text-[11px] font-bold tracking-[0.08em] text-faint">{g.title}</div>}
            {g.items.map((n) => <SideItem key={n.to} n={n} rail={rail} count={activity[n.to] ?? 0} />)}
          </div>
        ))}
        {rail ? null : <StorageCard />}
        {rail ? null : <VersionLine />}
      </aside>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Header live={live} />
        <main ref={mainRef} className="flex min-h-0 flex-1 flex-col overflow-y-auto">
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
                  <span className="relative"><Icon name={n.icon} /><ActivityBadge count={activity[n.to] ?? 0} className="absolute -right-3 -top-1.5 h-4 min-w-4 text-[10px]" /></span>
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

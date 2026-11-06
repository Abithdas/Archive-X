import { Link, Outlet } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { useLiveStatus } from '@/api/live'
import { Button } from '@/design/Button'
import { cn } from '@/design/cn'

const NAV = [
  { to: '/', label: 'Videos', icon: '▶' },
  { to: '/performers', label: 'Performers', icon: '☺' },
  { to: '/studios', label: 'Studios', icon: '▦' },
  { to: '/operations', label: 'Operations', icon: '∿' },
  { to: '/transfers', label: 'Transfers', icon: '⇄' },
  { to: '/settings', label: 'Settings', icon: '⚙' },
] as const

function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'
  document.documentElement.dataset.theme = next
  try {
    localStorage.setItem('ax_theme_mode', next)
  } catch {
    /* private mode */
  }
}

function NavLink({ to, label, icon, bottom }: { to: string; label: string; icon: string; bottom?: boolean }) {
  return (
    <Link
      to={to}
      activeOptions={{ exact: to === '/' }}
      className={cn(
        'flex items-center gap-3 rounded-xl text-sm font-semibold text-muted transition-colors hover:bg-surface-2 hover:text-fg',
        bottom ? 'min-h-12 flex-1 flex-col justify-center gap-0.5 px-1 text-[10.5px]' : 'min-h-11 px-3',
      )}
      activeProps={{ className: 'bg-surface-2 !text-fg' }}
    >
      <span aria-hidden="true" className="w-5 text-center text-base">
        {icon}
      </span>
      <span className={cn(bottom && 'truncate')}>{label}</span>
    </Link>
  )
}

export function Shell(): ReactNode {
  const live = useLiveStatus(true)
  return (
    <div className="flex h-full flex-col md:flex-row">
      <aside className="hidden w-56 shrink-0 flex-col gap-1 border-r border-line bg-surface p-3 md:flex" aria-label="Main">
        <div className="mb-3 flex items-center gap-2 px-2 pt-1 text-lg font-extrabold">
          <span className="grid size-8 place-items-center rounded-lg bg-accent text-xs text-accent-fg">AX</span>
          Archive X
        </div>
        {NAV.map((n) => (
          <NavLink key={n.to} {...n} />
        ))}
        <div className="mt-auto grid gap-2 p-1">
          <span className="flex items-center gap-2 text-xs text-muted" role="status">
            <span className={cn('size-2 rounded-full', live === 'live' ? 'bg-good' : live === 'connecting' ? 'bg-warn' : 'bg-bad')} />
            {live === 'live' ? 'Live' : live === 'connecting' ? 'Connecting…' : 'Offline, retrying'}
          </span>
          <Button onClick={toggleTheme} variant="ghost" className="justify-start">
            Toggle theme
          </Button>
          <a className="px-1 text-xs text-muted underline" href="/">
            Open classic interface
          </a>
        </div>
      </aside>
      <main className="min-h-0 flex-1 overflow-y-auto">
        <Outlet />
      </main>
      <nav className="flex border-t border-line bg-surface pb-[env(safe-area-inset-bottom)] md:hidden" aria-label="Main">
        {NAV.slice(0, 5).map((n) => (
          <NavLink key={n.to} {...n} bottom />
        ))}
      </nav>
    </div>
  )
}

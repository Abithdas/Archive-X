import { Link } from '@tanstack/react-router'
import { cn } from '@/design/cn'
import { motion, spring } from '@/design/motion'

const TABS = [{ to: '/movies', id: 'movies', label: 'Movies' }, { to: '/series', id: 'series', label: 'Series' }] as const

/** Movies | Series: one place in the menu, a switch inside it, the way Photos | People works. */
export function ScreenTabs({ active }: { active: 'movies' | 'series' }) {
  return (
    <div role="tablist" aria-label="Movies and series" className="mb-4 inline-flex gap-0.5 rounded-xl border border-line bg-surface p-[3px]">
      {TABS.map((t) => (
        <Link key={t.id} to={t.to} role="tab" aria-selected={active === t.id} className={cn('relative h-9 rounded-[9px] px-4 text-sm font-semibold leading-9 transition-colors', active === t.id ? 'text-fg' : 'text-muted hover:text-fg')}>
          {active === t.id ? <motion.span layoutId="screen-view-pill" transition={spring} aria-hidden="true" className="absolute inset-0 rounded-[9px] bg-surface-3" /> : null}
          <span className="relative">{t.label}</span>
        </Link>
      ))}
    </div>
  )
}

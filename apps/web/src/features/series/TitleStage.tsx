import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { Icon } from '@/design/Icon'
import { PLACEHOLDER } from './PosterCard'

/**
 * The page of one title, laid out like a streaming app: the artwork fills the page and fades into it, what the title is sits on the left,
 * and a panel on the right holds what to watch (the episodes of a season, the copies of a movie).
 */
export function TitleStage({ backdrop, back, onBack, backLabel, left, panel, panelLabel }: {
  backdrop?: string; back: string; onBack?: () => void; backLabel: string; left: ReactNode; panel?: ReactNode; panelLabel: string
}) {
  return (
    <div className="relative isolate min-h-[calc(100dvh-64px)] overflow-hidden">
      {backdrop ? (
        <>
          <img src={backdrop || PLACEHOLDER} alt="" aria-hidden="true" className="mo-fade-in absolute inset-0 -z-20 size-full object-cover object-[70%_20%]" />
          <div aria-hidden="true" className="absolute inset-0 -z-10 bg-[linear-gradient(90deg,color-mix(in_srgb,var(--color-bg)_96%,transparent)_0%,color-mix(in_srgb,var(--color-bg)_70%,transparent)_38%,color-mix(in_srgb,var(--color-bg)_12%,transparent)_72%),linear-gradient(0deg,color-mix(in_srgb,var(--color-bg)_95%,transparent)_0%,transparent_45%)]" />
        </>
      ) : null}
      <div className="grid gap-6 px-4 pb-8 pt-4 sm:px-10 lg:grid-cols-[minmax(0,1fr)_minmax(340px,420px)] lg:gap-10">
        <div className="flex min-h-[60vh] flex-col lg:min-h-[calc(100dvh-110px)]">
          <Link to={back} onClick={onBack} aria-label={backLabel} className="grid size-10 place-items-center self-start rounded-full bg-fg/10 text-fg backdrop-blur transition-colors hover:bg-fg/20"><Icon name="arrow-left" className="size-5" /></Link>
          {left}
        </div>
        {panel ? <aside aria-label={panelLabel} className="mo-panel-in self-start rounded-[26px] border border-line bg-surface/80 p-4 shadow-2xl backdrop-blur-xl lg:sticky lg:top-20 lg:max-h-[calc(100dvh-100px)] lg:overflow-y-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">{panel}</aside> : null}
      </div>
    </div>
  )
}

/** A short list of names as chips, under a small heading (genres, cast, directors). */
export function Chips({ label, items, title }: { label: string; items: string[]; title?: (s: string) => string | undefined }) {
  if (!items.length) return null
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="w-20 text-[11px] font-bold uppercase tracking-wide text-faint">{label}</span>
      <ul aria-label={label} className="m-0 flex list-none flex-wrap gap-2 p-0">{items.map((g) => <li key={g} title={title?.(g)} className="rounded-full bg-fg/10 px-3.5 py-1.5 text-[13px] font-semibold backdrop-blur">{g}</li>)}</ul>
    </div>
  )
}

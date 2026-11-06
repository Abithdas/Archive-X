import type { ReactNode } from 'react'

/** Eyebrow, title and subtitle: the heading block every classic page starts with. */
export function PageHead({ eyebrow = 'Library', title, blurb, children }: { eyebrow?: string; title: string; blurb?: string; children?: ReactNode }) {
  return (
    <header className="mb-5 flex flex-wrap items-end justify-between gap-4">
      <div>
        <p className="mb-1.5 text-[11px] font-bold uppercase tracking-[0.12em] text-accent">{eyebrow}</p>
        <h1 className="text-[clamp(25px,2vw,32px)] font-extrabold leading-[1.12] tracking-[-0.03em]">{title}</h1>
        {blurb ? <p className="mt-1.5 text-[15px] text-muted">{blurb}</p> : null}
      </div>
      {children}
    </header>
  )
}

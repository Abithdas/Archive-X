import { useEffect, useRef, type ReactNode } from 'react'
import { ApiError } from '@/api/client'
import { cn } from '@/design/cn'

export const CARD = 'flex flex-col rounded-[18px] border border-line bg-surface p-4 sm:p-5'
export const FIELD = 'h-11 w-full min-w-0 rounded-xl border border-line-2 bg-bg px-3 text-sm text-fg outline-none placeholder:text-faint focus:border-accent/60'
export const detail = (e: unknown, fallback: string) => (e instanceof ApiError ? e.detail || fallback : fallback)

export function Check({ checked, onChange, title, hint, disabled, icon }: { checked: boolean; onChange: (v: boolean) => void; title: string; hint?: string; disabled?: boolean; icon?: ReactNode }) {
  return (
    <label className={cn('relative flex cursor-pointer items-start gap-3 rounded-xl p-2 hover:bg-fg/5', !!icon && 'items-center', !!icon && checked && 'bg-good/10', disabled && 'cursor-not-allowed opacity-50')}>
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} className={icon ? 'peer sr-only' : 'mt-1 size-4 shrink-0 accent-[var(--color-accent)]'} />
      {icon ? (
        // Channels: no checkbox; a small green tick on the picture shows what is selected.
        <span className="relative grid size-11 shrink-0 place-items-center rounded-full bg-surface-2 text-xs font-bold peer-focus-visible:ring-2 peer-focus-visible:ring-accent">
          {icon}
          {checked ? <span aria-hidden="true" className="absolute -right-1 -top-1 grid size-[18px] place-items-center rounded-full bg-good text-white ring-2 ring-surface"><svg viewBox="0 0 24 24" className="size-3" fill="none" stroke="currentColor" strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg></span> : null}
        </span>
      ) : null}
      <span className="min-w-0"><b className="block text-sm">{title}</b>{hint ? <small className="text-xs text-muted">{hint}</small> : null}</span>
    </label>
  )
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="grid gap-1.5 text-xs font-semibold text-muted">
      {label}
      {children}
      {hint ? <span className="text-[11px] font-normal text-faint">{hint}</span> : null}
    </label>
  )
}

/** Numbered step pills with a progress bar; the active step scrolls into view on a phone. */
export function Stepper({ steps, step, onStep, canGo }: { steps: readonly string[]; step: number; onStep: (i: number) => void; canGo: boolean }) {
  const list = useRef<HTMLOListElement>(null)
  useEffect(() => {
    list.current?.querySelector<HTMLElement>('[aria-current=step]')?.scrollIntoView?.({ inline: 'center', block: 'nearest' })
  }, [step])
  const pct = Math.round(((step + 1) / steps.length) * 100)
  return (
    <>
      <ol ref={list} className="my-4 flex max-w-full gap-2 overflow-x-auto [scrollbar-width:none]" aria-label="Steps">
        {steps.map((s, i) => (
          <li key={s}>
            <button type="button" aria-current={i === step ? 'step' : undefined} onClick={() => (i <= step || canGo) && onStep(i)} className={cn('flex h-10 items-center gap-2 whitespace-nowrap rounded-full px-3 text-sm font-semibold', i === step ? 'bg-surface-3 text-fg' : 'text-muted')}>
              <span className={cn('grid size-6 place-items-center rounded-full text-xs font-bold', i === step ? 'bg-accent text-accent-fg' : i < step ? 'bg-good/20 text-good' : 'bg-surface-3')}>{i + 1}</span>
              {s}
            </button>
          </li>
        ))}
      </ol>
      <div className="mb-4 h-1 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-label="Workflow progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}><span className="block h-full bg-accent transition-[width]" style={{ width: `${pct}%` }} /></div>
    </>
  )
}

/** "12 videos" for a channel row; a channel that has not been scanned yet counts as 0 rather than showing "undefined". */
export const videosHint = (n: unknown): string => { const v = Number(n); const c = Number.isFinite(v) && v > 0 ? v : 0; return `${c.toLocaleString()} video${c === 1 ? '' : 's'}` }

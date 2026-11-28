import { Link } from '@tanstack/react-router'
import type { ReactNode } from 'react'
import { cn } from '@/design/cn'

export const PLACEHOLDER = '/static/icons/ax-placeholder.png'

const FRAME = 'mo-poster relative block aspect-[2/3] w-full overflow-hidden rounded-lg border border-white/10 bg-surface-2 text-left'

/**
 * One poster, the way a streaming app shows it: the picture with a rating (and a note) on it, the title and a line under it.
 * It is a link when it has somewhere to go (`to`), a button otherwise; the pointer resting on it (`onHover`) lets a page show its details.
 */
export function PosterCard({ to, params, onClick, onHover, image, title, sub, rating, badge, corner, active, label, className }: {
  to?: string; params?: Record<string, string>; onClick?: () => void; onHover?: (el: HTMLElement) => void
  image: string; title: string; sub?: string; rating?: string | number | null; badge?: string; corner?: ReactNode; active?: boolean; label?: string; className?: string
}) {
  const inner = (
    <>
      <img src={image || PLACEHOLDER} alt="" loading="lazy" decoding="async" className="absolute inset-0 size-full object-cover" />
      {rating ? <span className="absolute left-2 top-2 inline-flex items-center gap-1 rounded bg-black/75 px-1 py-0.5 text-[11px] font-bold leading-none text-white"><span aria-hidden="true" className="rounded-[3px] bg-[#f5c518] px-1 py-0.5 text-[10px] font-black text-black">IMDb</span>{rating}</span> : null}
      {badge ? <span className={cn('absolute rounded-full bg-black/70 px-2 py-0.5 text-[11px] font-bold text-white', rating ? 'left-2 top-9' : 'left-2 top-2')}>{badge}</span> : null}
      {corner ? <span className="absolute right-2 top-2">{corner}</span> : null}
    </>
  )
  const hover = onHover ? { onPointerEnter: (e: React.PointerEvent) => { if (e.pointerType === 'mouse') onHover(e.currentTarget as HTMLElement) }, onFocus: (e: React.FocusEvent) => onHover(e.currentTarget as HTMLElement) } : {}
  return (
    <div className={cn('grid gap-2', className)}>
      {to
        ? <Link to={to} params={params} aria-label={label ?? `Open ${title}`} data-active={active || undefined} className={FRAME} {...hover}>{inner}</Link>
        : <button type="button" onClick={onClick} aria-label={label ?? `Details of ${title}`} data-active={active || undefined} className={FRAME} {...hover}>{inner}</button>}
      {/* like dx.bi, the poster stands on its own; the name is on the preview that opens when the pointer rests on it (and for screen readers here) */}
      <div className="sr-only"><p>{title}</p>{sub ? <p>{sub}</p> : null}</div>
    </div>
  )
}

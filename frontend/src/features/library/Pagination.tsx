import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { PER_CHOICES } from './store'

/** First, last and a window around the current page, with "…" for the gaps: 1 2 3 … 6 7 8. */
export function pageWindow(current: number, total: number): Array<number | '…'> {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  const set = new Set([1, 2, total - 1, total, current - 1, current, current + 1].filter((n) => n >= 1 && n <= total))
  const out: Array<number | '…'> = []
  let prev = 0
  for (const n of [...set].sort((a, b) => a - b)) {
    if (n - prev > 1) out.push('…')
    out.push(n)
    prev = n
  }
  return out
}

const BTN = 'grid h-9 min-w-9 place-items-center rounded-lg border border-line bg-surface px-2 text-sm font-semibold hover:bg-surface-2 disabled:opacity-40'

export function Pagination({ page, pages, per, onPage, onPer }: { page: number; pages: number; per: number; onPage: (zeroBased: number) => void; onPer: (n: number) => void }) {
  const cur = page + 1
  return (
    <nav aria-label="Pages" className="mt-8 flex flex-wrap items-center justify-between gap-3 text-sm text-muted">
      <div className="flex items-center gap-2">
        <label className="flex items-center gap-2">
          Page
          <input
            key={cur}
            type="number"
            min={1}
            max={pages}
            defaultValue={cur}
            aria-label="Go to page"
            onKeyDown={(e) => {
              if (e.key === 'Enter') onPage(Math.min(pages, Math.max(1, Number(e.currentTarget.value) || 1)) - 1)
            }}
            className="h-9 w-16 rounded-lg border border-line-2 bg-bg px-2 text-center text-fg"
          />
          of {pages}
        </label>
        <label className="ml-2 flex items-center gap-2">
          Items per page
          <select value={per} onChange={(e) => onPer(Number(e.target.value))} className="h-9 rounded-lg border border-line-2 bg-bg px-2 text-fg">
            {PER_CHOICES.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
      </div>
      <div className="flex items-center gap-1">
        <button type="button" className={BTN} disabled={cur <= 1} onClick={() => onPage(0)} aria-label="First page">≪</button>
        <button type="button" className={BTN} disabled={cur <= 1} onClick={() => onPage(page - 1)} aria-label="Previous page"><Icon name="arrow-left" className="size-4" /></button>
        {pageWindow(cur, pages).map((n, i) =>
          n === '…' ? <span key={`g${i}`} className="px-1">…</span> : (
            <button key={n} type="button" aria-current={n === cur ? 'page' : undefined} onClick={() => onPage(n - 1)} className={cn(BTN, n === cur && 'border-accent/50 bg-accent/15 text-accent')}>{n}</button>
          ),
        )}
        <button type="button" className={BTN} disabled={cur >= pages} onClick={() => onPage(page + 1)} aria-label="Next page"><Icon name="arrow-right" className="size-4" /></button>
        <button type="button" className={BTN} disabled={cur >= pages} onClick={() => onPage(pages - 1)} aria-label="Last page">≫</button>
      </div>
    </nav>
  )
}

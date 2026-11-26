import { Link } from '@tanstack/react-router'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { PLACEHOLDER } from './PosterCard'

/** What a preview shows: what the row's list already knows about the title. */
export interface Previewable { imdb: string; kind: 'tv' | 'movie'; title: string; year: number | null; poster: string; background: string; rating: string; overview: string; genres: string[]; library: { kind: 'tv' | 'movie'; id: number } | null }
const hasPage = (item: Previewable) => /^(tt\d+|tmdb-\d+|kitsu:\d+)$/.test(item.imdb)
const OPEN_AFTER_MS = 450
const CLOSE_AFTER_MS = 140
const WIDTH = 340

interface Shown { item: Previewable; rect: DOMRect }

/**
 * Rest the pointer on a card of a row and its preview unfolds out of it (the way dx.bi does): a wide picture that pushes in slowly, the title,
 * rating and genres, a few lines of the story and a way to open the page. It stays while the pointer is on it and closes shortly after the pointer leaves.
 */
export function useRowPreview(): { hover: (item: Previewable) => (el: HTMLElement) => void; node: ReactNode } {
  const [shown, setShown] = useState<Shown | null>(null)
  const open = useRef<ReturnType<typeof setTimeout>>(undefined)
  const close = useRef<ReturnType<typeof setTimeout>>(undefined)
  const card = useRef<HTMLElement | null>(null)
  const hide = useCallback(() => { clearTimeout(open.current); clearTimeout(close.current); close.current = setTimeout(() => { card.current = null; setShown(null) }, CLOSE_AFTER_MS) }, [])
  // The pointer is "on it" while it is inside the card or the preview (by position: the browser's own leave events misfire when a card moves under the pointer as it grows).
  const inside = useCallback((x: number, y: number) => {
    const within = (r: DOMRect | undefined) => Boolean(r) && x >= r!.left - 4 && x <= r!.right + 4 && y >= r!.top - 4 && y <= r!.bottom + 4
    return within(card.current?.getBoundingClientRect()) || within(document.querySelector('[data-row-preview]')?.getBoundingClientRect())
  }, [])
  const hover = useCallback((item: Previewable) => (el: HTMLElement) => {
    if (typeof window.matchMedia === 'function' && !window.matchMedia('(hover: hover)').matches) return   // touch: the card simply opens
    clearTimeout(open.current); clearTimeout(close.current)
    card.current = el
    open.current = setTimeout(() => { if (el.isConnected) setShown({ item, rect: el.getBoundingClientRect() }) }, OPEN_AFTER_MS)
  }, [])
  useEffect(() => {
    const move = (e: PointerEvent) => {
      if (!card.current) return
      if (inside(e.clientX, e.clientY)) clearTimeout(close.current)
      else hide()
    }
    const away = () => { clearTimeout(open.current); card.current = null; setShown(null) }
    document.addEventListener('pointermove', move)
    window.addEventListener('scroll', away, true)
    window.addEventListener('resize', away)
    return () => { document.removeEventListener('pointermove', move); window.removeEventListener('scroll', away, true); window.removeEventListener('resize', away); clearTimeout(open.current); clearTimeout(close.current) }
  }, [hide, inside])
  const node = shown ? <Preview key={`${shown.item.kind}:${shown.item.imdb}`} {...shown} onDone={() => setShown(null)} /> : null
  return { hover, node }
}

function Preview({ item, rect, onDone }: Shown & { onDone: () => void }) {
  const vw = window.innerWidth, vh = window.innerHeight
  const width = Math.min(WIDTH, vw - 24)
  const left = Math.max(12, Math.min(vw - width - 12, rect.left + rect.width / 2 - width / 2))
  const top = Math.max(12, Math.min(vh - 320, rect.top - 16))
  const meta = [item.year, item.kind === 'tv' ? 'Series' : 'Movie'].filter(Boolean).join(' · ')
  const art = (
    <span className="relative block aspect-video w-full overflow-hidden rounded-t-[18px] bg-surface-3">
      <img src={item.background || item.poster || PLACEHOLDER} alt="" className="mo-preview-art absolute inset-0 size-full object-cover" />
      <span aria-hidden="true" className="absolute inset-0 bg-[linear-gradient(0deg,var(--color-surface)_0%,transparent_55%)]" />
      <span className="absolute inset-x-3 bottom-2 text-lg font-extrabold leading-tight text-white drop-shadow-lg">{item.title}</span>
    </span>
  )
  return (
    <div role="dialog" data-row-preview aria-label={`Preview of ${item.title}`} style={{ left, top, width }}
      className="mo-unfold fixed z-50 overflow-hidden rounded-[18px] border border-white/10 bg-surface shadow-[0_18px_60px_rgb(0_0_0/0.6)] ring-1 ring-accent/30">
      {hasPage(item) ? <Link to="/discover/$kind/$id" params={{ kind: item.kind === 'tv' ? 'series' : 'movie', id: item.imdb }} onClick={onDone} aria-label={`Open ${item.title}`} className="block">{art}</Link> : art}
      <div className="mo-preview-body grid gap-2 p-3.5">
        <p className="m-0 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] font-semibold">
          {item.rating ? <span className="inline-flex items-center gap-1"><span aria-hidden="true" className="text-amber-300">★</span>{item.rating}</span> : null}
          {meta ? <span className="text-muted">{meta}</span> : null}
          {item.library ? <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] font-bold text-accent-fg">In your library</span> : null}
        </p>
        {item.genres.length ? <p className="m-0 text-xs text-muted">{item.genres.slice(0, 3).join(' · ')}</p> : null}
        {item.overview ? <p className="m-0 line-clamp-3 text-[13px] leading-snug text-fg/85">{item.overview}</p> : null}
      </div>
    </div>
  )
}

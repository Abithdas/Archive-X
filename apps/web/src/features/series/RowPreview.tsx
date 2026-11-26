import { Link } from '@tanstack/react-router'
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { PLACEHOLDER } from './PosterCard'

/** What a preview shows: what the row's list already knows about the title. */
export interface Previewable { imdb: string; kind: 'tv' | 'movie'; title: string; year: number | null; poster: string; background: string; rating: string; overview: string; genres: string[]; library: { kind: 'tv' | 'movie'; id: number } | null }
const hasPage = (item: Previewable) => /^(tt\d+|tmdb-\d+|kitsu:\d+)$/.test(item.imdb)
const OPEN_AFTER_MS = 450
const CLOSE_AFTER_MS = 140
const CLOSING_MS = 200
const EASE = 'cubic-bezier(0.2, 0.85, 0.25, 1)'
const reduced = () => typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches && document.documentElement.dataset.motion !== 'full'

interface Shown { item: Previewable; card: HTMLElement; closing?: boolean }

/**
 * Rest the pointer on a card of a row and its preview unfolds out of the card, the way dx.bi's does: the panel is revealed from the poster's own
 * outline outwards while the poster fades into the panel's picture, the titles beside it glide aside to make room, and a soft glow of the title's
 * colours comes up around it. It stays while the pointer is on the card or the panel and closes shortly after the pointer has left.
 */
export function useRowPreview(): { hover: (item: Previewable) => (el: HTMLElement) => void; node: ReactNode } {
  const [shown, setShown] = useState<Shown | null>(null)
  const open = useRef<ReturnType<typeof setTimeout>>(undefined)
  const close = useRef<ReturnType<typeof setTimeout>>(undefined)
  const gone = useRef<ReturnType<typeof setTimeout>>(undefined)
  const card = useRef<HTMLElement | null>(null)
  const rowAt = useRef<{ row: HTMLElement | null | undefined; left: number }>({ row: null, left: 0 })
  const end = useCallback((quick?: boolean) => {
    clearTimeout(gone.current)
    card.current = null
    if (quick || reduced()) return setShown(null)
    setShown((s) => (s ? { ...s, closing: true } : s))
    gone.current = setTimeout(() => setShown(null), CLOSING_MS)
  }, [])
  const hide = useCallback(() => { clearTimeout(open.current); clearTimeout(close.current); close.current = setTimeout(() => end(), CLOSE_AFTER_MS) }, [end])
  // The pointer is "on it" while it is inside the card or the preview (by position: the browser's own leave events misfire when a card moves under the pointer as it grows).
  const inside = useCallback((x: number, y: number) => {
    const within = (r: DOMRect | undefined) => Boolean(r) && x >= r!.left - 4 && x <= r!.right + 4 && y >= r!.top - 4 && y <= r!.bottom + 4
    return within(card.current?.getBoundingClientRect()) || within(document.querySelector('[data-row-preview]')?.getBoundingClientRect())
  }, [])
  const hover = useCallback((item: Previewable) => (el: HTMLElement) => {
    if (typeof window.matchMedia === 'function' && !window.matchMedia('(hover: hover)').matches) return   // touch: the card simply opens
    clearTimeout(open.current); clearTimeout(close.current); clearTimeout(gone.current)
    card.current = el
    open.current = setTimeout(() => { if (el.isConnected) { const row = el.parentElement?.parentElement; rowAt.current = { row, left: row?.scrollLeft ?? 0 }; setShown({ item, card: el }) } }, OPEN_AFTER_MS)
  }, [])
  useEffect(() => {
    const move = (e: PointerEvent) => {
      if (!card.current) return
      if (inside(e.clientX, e.clientY)) clearTimeout(close.current)
      else hide()
    }
    // the row settling under the neighbours that glide aside is not the pointer scrolling it: only a real move of the row (or the page) closes the preview
    const away = (e?: Event) => { if (e && e.target instanceof Node && document.querySelector('[data-row-preview]')?.contains(e.target)) return; if (e && e.target === rowAt.current.row && Math.abs((rowAt.current.row?.scrollLeft ?? 0) - rowAt.current.left) < 2) return; clearTimeout(open.current); end(true) }
    document.addEventListener('pointermove', move)
    window.addEventListener('scroll', away, true)
    window.addEventListener('resize', away)
    return () => { document.removeEventListener('pointermove', move); window.removeEventListener('scroll', away, true); window.removeEventListener('resize', away); clearTimeout(open.current); clearTimeout(close.current); clearTimeout(gone.current) }
  }, [hide, inside, end])
  const node = shown ? <Preview key={`${shown.item.kind}:${shown.item.imdb}`} {...shown} onDone={() => end(true)} /> : null
  return { hover, node }
}

interface Geometry { left: number; top: number; width: number; from: string; ghost: { left: number; top: number; width: number; height: number }; pushes: Array<[HTMLElement, number]> }

function Preview({ item, card, closing, onDone }: Shown & { onDone: () => void }) {
  const box = useRef<HTMLDivElement>(null)
  const [geo, setGeo] = useState<Geometry | null>(null)
  const [opened, setOpened] = useState(false)
  const still = reduced()
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
  const wrapper = card.parentElement   // the poster's own box in the row: its neighbours are the other titles
  const seen = card.getBoundingClientRect()
  const [rect] = useState(() => seen)
  const width = Math.round(Math.min(Math.max(rect.width * 1.72, 19 * rem), Math.min(30 * rem, window.innerWidth - 24)))

  // Final geometry: wider than the card, centred on it, always fully on screen (at either end it grows inwards from the card's own edge).
  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const height = el.offsetHeight
    const margin = 12
    const navBottom = 4.25 * rem + 8
    let left = rect.left + rect.width / 2 - width / 2
    if (left < margin) left = Math.max(margin, rect.left)
    else if (left + width > window.innerWidth - margin) left = Math.min(window.innerWidth - margin, rect.right) - width
    const top = Math.max(navBottom, Math.min(rect.top + rect.height / 2 - height / 2, window.innerHeight - height - margin))
    const px = (v: number) => `${Math.max(0, Math.round(v))}px`
    const row = wrapper?.parentElement
    const siblings = row ? [...row.children] as HTMLElement[] : []
    const gap = siblings.length > 1 ? Math.max(4, siblings[1]!.offsetLeft - siblings[0]!.offsetLeft - siblings[0]!.offsetWidth) : 10
    const at = wrapper ? siblings.indexOf(wrapper) : -1
    const pushes: Array<[HTMLElement, number]> = at < 0 ? [] : siblings.filter((_, i) => i !== at).map((n, i) => [n, (i < at ? -1 : 1) * ((i < at ? Math.max(0, rect.left - left) : Math.max(0, left + width - rect.right)) + gap)])
    setGeo({ left: Math.round(left), top: Math.round(top), width, pushes,
      from: `inset(${px(rect.top - top)} ${px(left + width - rect.right)} ${px(top + height - rect.bottom)} ${px(rect.left - left)} round 8px)`,
      ghost: { left: Math.round(rect.left - left), top: Math.round(rect.top - top), width: Math.round(rect.width), height: Math.round(rect.height) } })
  }, [rect, rem, width, wrapper])

  // The next frame after it is placed: reveal it, push the neighbours aside and let the card's own poster step out once the panel has grown over it.
  useEffect(() => {
    if (!geo) return
    const row = wrapper?.parentElement
    const raf = requestAnimationFrame(() => {
      setOpened(true)
      if (row && geo.pushes.length) row.style.scrollSnapType = 'none'   // rows snap to their titles: pushing titles aside moves the snap points, and the re-snap would scroll the row and close this

      if (!still) for (const [n, dx] of geo.pushes) { n.style.transition = `transform .46s ${EASE}`; n.style.transform = `translate3d(${Math.round(dx)}px,0,0)` }
      card.style.transition = 'opacity .15s .2s'; card.style.opacity = '0'
    })
    return () => {
      cancelAnimationFrame(raf)
      card.style.opacity = ''
      if (row) setTimeout(() => { row.style.scrollSnapType = '' }, 500)
      for (const [n] of geo.pushes) { n.style.transform = ''; setTimeout(() => { n.style.transition = '' }, 500) }
    }
  }, [geo, card, still, wrapper])

  const meta = [item.year, item.kind === 'tv' ? 'Series' : 'Movie'].filter(Boolean).join(' · ')
  const art = item.background || item.poster || PLACEHOLDER
  const on = opened && !closing
  const media = (
    <span className="relative block aspect-video w-full overflow-hidden bg-surface-3">
      <img src={art} alt="" className={on ? 'mo-preview-art absolute inset-0 size-full object-cover' : 'absolute inset-0 size-full object-cover'} />
      <span aria-hidden="true" className="absolute inset-0 bg-[linear-gradient(0deg,#181818_0%,rgba(24,24,24,0.35)_22%,transparent_45%)]" />
      <span className="absolute inset-x-3.5 bottom-2.5 text-lg font-extrabold leading-tight text-white drop-shadow-lg">{item.title}</span>
    </span>
  )
  return (
    <div ref={box} role="dialog" data-row-preview aria-label={`Preview of ${item.title}`}
      style={{ position: 'fixed', zIndex: 900, left: geo?.left ?? 0, top: geo?.top ?? 0, width, minHeight: Math.round(rect.height), visibility: geo ? 'visible' : 'hidden',
        opacity: closing ? 0 : 1, transform: closing ? 'scale(0.97)' : 'none', transition: still ? 'none' : closing ? `transform ${CLOSING_MS}ms ease, opacity ${CLOSING_MS}ms ease` : 'opacity .1s ease' }}>
      {/* a deep shadow underneath for lift, and the title's own colours glowing softly around it once it has opened */}
      <span aria-hidden="true" style={{ position: 'absolute', inset: '10% 6% -4%', zIndex: -2, borderRadius: 18, background: 'rgba(0,0,0,.85)', filter: 'blur(28px)', opacity: on ? 1 : 0, transition: still ? 'none' : 'opacity .4s ease .12s', pointerEvents: 'none' }} />
      <span aria-hidden="true" style={{ position: 'absolute', inset: '-14px -18px -22px', zIndex: -1, borderRadius: 22, background: `#181818 center / cover no-repeat url("${art}")`, filter: 'blur(30px) saturate(1.5) brightness(.9)', opacity: on ? 0.55 : 0, transform: on ? 'none' : 'scale(.92)', transition: still ? 'none' : `opacity .55s ease .16s, transform .6s ${EASE} .1s`, pointerEvents: 'none' }} />
      <div style={{ position: 'relative', minHeight: 'inherit', display: 'flex', flexDirection: 'column', borderRadius: 8, background: '#181818', overflow: 'hidden', clipPath: geo && !on && !closing ? geo.from : 'inset(0 round 8px)', transition: still ? 'none' : `clip-path .48s ${EASE}`, boxShadow: 'inset 0 0 0 1px rgba(255,255,255,.08)' }}>
        {hasPage(item) ? <Link to="/discover/$kind/$id" params={{ kind: item.kind === 'tv' ? 'series' : 'movie', id: item.imdb }} onClick={onDone} aria-label={`Open ${item.title}`} className="block">{media}</Link> : media}
        <div className={on ? 'mo-preview-body grid gap-2 p-3.5' : 'grid gap-2 p-3.5 opacity-0'}>
          <p className="m-0 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] font-semibold">
            {item.rating ? <span className="inline-flex items-center gap-1"><span aria-hidden="true" className="text-amber-300">★</span>{item.rating}</span> : null}
            {meta ? <span className="text-muted">{meta}</span> : null}
            {item.library ? <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] font-bold text-accent-fg">In your library</span> : null}
          </p>
          {item.genres.length ? <p className="m-0 text-xs text-muted">{item.genres.slice(0, 3).join(' · ')}</p> : null}
          {item.overview ? <p className="m-0 line-clamp-3 text-[13px] leading-snug text-fg/85">{item.overview}</p> : null}
        </div>
        {/* the card's own poster, fading into the panel's picture as it unfolds */}
        {geo ? <span aria-hidden="true" style={{ position: 'absolute', zIndex: 5, left: geo.ghost.left, top: geo.ghost.top, width: geo.ghost.width, height: geo.ghost.height, borderRadius: 8, background: `#222 center / cover no-repeat url("${item.poster || art}")`, pointerEvents: 'none', opacity: on || closing ? 0 : 1, transition: still ? 'none' : 'opacity .3s ease .16s' }} /> : null}
      </div>
    </div>
  )
}

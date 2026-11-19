import { dropdownPosition, limitMenu, openHeight } from '@/design/placement'
import { useId, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Icon } from '@/design/Icon'
import { Skeleton } from '@/design/Skeleton'
import { cn } from '@/design/cn'
import { photoName, ratio, type Photo } from './photos'
import { LikeHeart } from '@/design/LikeHeart'

const OPTION = 'flex h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-[15px] font-medium hover:bg-fg/5 focus-visible:bg-fg/5 focus-visible:outline-none'

/** One photo tile at its own aspect ratio, with a ⋯ menu (open, copy filename, remove, delete from Telegram too). */
export function PhotoCard({ photo, favorite, onOpen, onRemove, onDifferent, onAddToPerson }: { photo: Photo; favorite: boolean; onOpen: () => void; onRemove: (remote: boolean) => void; onDifferent?: () => void; onAddToPerson?: () => void }) {
  const id = useId()
  // The tile shimmers until its picture has arrived, then the picture fades in (a cached picture shows at once).
  const [ready, setReady] = useState(false)
  const [failed, setFailed] = useState(false)
  const pop = useRef<HTMLDivElement>(null)
  const btn = useRef<HTMLButtonElement>(null)
  const name = photoName(photo)
  const place = () => {
    const el = pop.current, b = btn.current?.getBoundingClientRect()
    if (!el || !b) return
    const { left, top, maxHeight } = dropdownPosition(b, btn.current, 260, openHeight(el) || 220, 'right')
    el.style.left = `${left}px`
    el.style.top = `${top}px`
    limitMenu(el, maxHeight)
  }
  const close = () => pop.current?.hidePopover?.()
  const copy = async () => {
    close()
    try { await navigator.clipboard.writeText(name); toast('Filename copied') } catch { toast.error('Could not copy the filename.') }
  }
  return (
    <article title={name} aria-label={name} className="group relative mb-2 block break-inside-avoid overflow-hidden rounded-xl border border-line bg-surface-2 sm:mb-3">
      <button type="button" onClick={onOpen} aria-label={`Open ${name}`} style={{ aspectRatio: ratio(photo) }} className="relative block w-full bg-surface-3">
        {!ready && !failed ? <Skeleton className="absolute inset-0 rounded-none" /> : null}
        <img ref={(el) => { if (el?.complete && el.naturalWidth > 0) setReady(true) }} src={photo.thumbnail_url} alt="" loading="lazy" decoding="async" draggable={false} onLoad={() => setReady(true)} onError={() => { setFailed(true); setReady(true) }}
          className={cn('absolute inset-0 size-full object-cover transition-[opacity,transform] duration-300 group-hover:scale-[1.03]', ready && !failed ? 'opacity-100' : 'opacity-0')} />
      </button>
      {favorite ? <span title="Favourite" aria-label="Favourite" className="pointer-events-none absolute left-2 top-2 grid size-7 place-items-center rounded-full bg-black/55 text-white"><LikeHeart liked /></span> : null}
      <button ref={btn} type="button" popoverTarget={id} aria-label={`More options for ${name}`} aria-haspopup="menu" title="More options" onClick={place}
        className={cn('absolute right-2 top-2 grid size-9 place-items-center rounded-full bg-black/55 text-white backdrop-blur-sm hover:bg-black/75 focus-visible:opacity-100 sm:opacity-0 sm:group-hover:opacity-100')}>
        <span className="flex gap-[3px]" aria-hidden="true"><i className="size-1 rounded-full bg-current" /><i className="size-1 rounded-full bg-current" /><i className="size-1 rounded-full bg-current" /></span>
      </button>
      <div ref={pop} id={id} popover="auto" role="menu" aria-label="Photo actions" onToggle={(e) => (e.nativeEvent as ToggleEvent).newState === 'open' && (place(), pop.current?.querySelector<HTMLElement>('button')?.focus())} className="mo-menu m-0 w-[260px] rounded-[16px] border border-line-2 bg-surface text-fg shadow-[0_18px_50px_rgb(0_0_0/0.5)]">
        <div className="mo-fold-inner p-2">
        <button type="button" role="menuitem" className={OPTION} onClick={() => { close(); onOpen() }}><Icon name="images" className="text-muted" />Open photo</button>
        <button type="button" role="menuitem" className={OPTION} onClick={() => void copy()}><Icon name="duplicate" className="text-muted" />Copy filename</button>
        {onAddToPerson ? <button type="button" role="menuitem" className={OPTION} onClick={() => { close(); onAddToPerson() }}><Icon name="user" className="text-muted" />Add to a person…</button> : null}
        {onDifferent ? <button type="button" role="menuitem" className={OPTION} onClick={() => { close(); onDifferent() }}><Icon name="user" className="text-muted" />Different person · remove from this group</button> : null}
        <div role="separator" className="my-1.5 h-px bg-line" />
        <button type="button" role="menuitem" className={OPTION} onClick={() => { close(); onRemove(false) }}><Icon name="trash" className="text-muted" />Remove from Archive X</button>
        <button type="button" role="menuitem" className={cn(OPTION, 'text-bad')} onClick={() => { close(); onRemove(true) }}><Icon name="trash" />Delete from Archive X and Telegram</button>
      </div>
      </div>
    </article>
  )
}

import { useCallback, useRef, useState, type ImgHTMLAttributes, type KeyboardEvent } from 'react'
import { PhotoViewer } from '@/features/photos/PhotoViewer'
import type { Photo } from '@/features/photos/photos'

/** One picture the viewer can show, read from a thumbnail on the page. */
interface Pic { el: HTMLImageElement; src: string; full: string; alt: string }

const picOf = (el: HTMLImageElement): Pic => ({ el, src: el.currentSrc || el.src, full: el.dataset.zoomFull || el.currentSrc || el.src, alt: el.alt })
const hidden = (el: HTMLElement) => el.style.display === 'none' || el.getClientRects().length === 0

/** The pictures that share a group with this thumbnail, in page order (just itself when it has none). */
export function picturesOf(thumb: HTMLImageElement): Pic[] {
  const group = thumb.dataset.zoomGroup
  if (!group) return [picOf(thumb)]
  const all = Array.from(document.querySelectorAll<HTMLImageElement>('img[data-zoomable]')).filter((el) => el.dataset.zoomGroup === group && (el === thumb || !hidden(el)))
  return all.length ? all.map(picOf) : [picOf(thumb)]
}

/** The viewer's own item for a picture: no size or date is known, so only the name is shown. */
const photoOf = (p: Pic, i: number, group: string): Photo => ({ id: i + 1, channel_id: group || 'zoom', message_id: i + 1, filename: p.alt, size: 0, width: 0, height: 0, thumbnail_url: p.src, preview_url: p.src, image_url: p.full })

/**
 * A picture that opens in the photo viewer when you click it: the same viewer as the Photos page (zoom, pan, rotate, full screen, keyboard,
 * arrows and a strip of the others), and it grows out of the thumbnail and goes back into it. Give pictures the same `group` to page between them.
 * `fullSrc` is the bigger picture to show once open, when the thumbnail is a small version.
 */
export function ZoomImage({ fullSrc, group, className, onClick, onKeyDown, ...img }: ImgHTMLAttributes<HTMLImageElement> & { src: string; fullSrc?: string; group?: string }) {
  const thumb = useRef<HTMLImageElement>(null)
  const [pics, setPics] = useState<Pic[] | null>(null)
  const [index, setIndex] = useState(0)
  const label = img.alt ? `Enlarge: ${img.alt}` : 'Enlarge image'

  const show = useCallback(() => {
    const el = thumb.current
    if (!el || pics) return
    const list = picturesOf(el)
    setIndex(Math.max(0, list.findIndex((p) => p.el === el)))
    setPics(list)
  }, [pics])
  const close = useCallback(() => {
    setPics(null)
    thumb.current?.focus({ preventScroll: true })
  }, [])

  return (
    <>
      <img
        {...img}
        ref={thumb}
        data-zoomable=""
        data-zoom-group={group}
        data-zoom-full={fullSrc}
        role="button"
        tabIndex={0}
        aria-label={label}
        aria-expanded={pics !== null}
        className={className}
        onClick={(e) => { onClick?.(e); if (!e.defaultPrevented) show() }}
        onKeyDown={(e: KeyboardEvent<HTMLImageElement>) => { onKeyDown?.(e); if ((e.key === 'Enter' || e.key === ' ') && !e.defaultPrevented) { e.preventDefault(); show() } }}
      />
      {pics ? (
        // The viewer is drawn on the page body, but React still passes its clicks up to this picture's parents (a card that opens something
        // when clicked, say): they are stopped here. Keys are not: the viewer listens for them on the document, and stopping them here would
        // stop them reaching it.
        <span style={{ display: 'contents' }} onClick={(e) => e.stopPropagation()}>
        <PhotoViewer
          items={pics.map((p, i) => photoOf(p, i, group ?? ''))}
          index={Math.min(index, pics.length - 1)}
          total={pics.length}
          onIndex={setIndex}
          onClose={close}
          origin={(i) => pics[i]?.el.isConnected ? pics[i]!.el.getBoundingClientRect() : null}
          referrerPolicy={img.referrerPolicy}
        />
        </span>
      ) : null}
    </>
  )
}

import { useState } from 'react'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'

/**
 * A trailer, played in the page with YouTube's own embedded player (the supported way to show a YouTube video in another app). The id comes from the
 * catalogue and is checked here; a trailer the owner does not allow to be embedded can still be watched on YouTube with the link under the player.
 */
export function TrailerButton({ id, title, className, label = 'Trailer' }: { id: string; title: string; className: string; label?: string }) {
  const [open, setOpen] = useState(false)
  if (!/^[\w-]{6,16}$/.test(id)) return null
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className={className}><Icon name="play" className="mr-1.5 size-3.5" />{label}</button>
      {open ? (
        <Dialog open onClose={() => setOpen(false)} title={`${title}: trailer`} wide>
          <div className="grid gap-2">
            <div className="aspect-video w-full overflow-hidden rounded-xl bg-black">
              <iframe title={`${title}: trailer`} src={`https://www.youtube-nocookie.com/embed/${id}?autoplay=1&rel=0&modestbranding=1`} allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen referrerPolicy="strict-origin-when-cross-origin" className="size-full border-0" />
            </div>
            <a href={`https://www.youtube.com/watch?v=${id}`} target="_blank" rel="noreferrer" className="w-fit text-[13px] font-semibold text-accent hover:underline">If it does not play, watch it on YouTube ↗</a>
          </div>
        </Dialog>
      ) : null}
    </>
  )
}

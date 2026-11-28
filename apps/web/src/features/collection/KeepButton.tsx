import { useState } from 'react'
import { toast } from 'sonner'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { errorText } from '@/features/content/useContentMode'
import { useCollection, useCollectionActions } from './useCollection'

/** "Keep this": have Archive X find and download the movie (or a show's episodes) by itself and keep the library up to date. A show can be kept in full or only from now on. */
export function KeepButton({ kind, imdb, title, year, poster, className, label }: { kind: 'movie' | 'series'; imdb: string; title: string; year?: number | null; poster?: string; className?: string; label?: string }) {
  const state = useCollection()
  const act = useCollectionActions()
  const [menu, setMenu] = useState(false)
  if (!/^tt\d{5,10}$/.test(imdb)) return null
  const kept = state.data?.titles.find((t) => t.imdb === imdb && t.monitored)      // a title only downloaded by hand is not kept
  const add = (scope: 'all' | 'new') => act.add.mutate({ kind, imdb, title, year: year ?? null, poster: poster ?? '', scope }, {
    onSuccess: () => { setMenu(false); toast(state.data?.settings.on ? `Keeping ${title}: Archive X will look for it now.` : `Added ${title} to Kept. Turn on automatic downloads in Downloads, Settings to start.`) },
    onError: (e) => toast.error(errorText(e, 'Could not keep it.')),
  })
  const base = 'inline-flex h-11 items-center gap-2 rounded-full px-6 text-sm font-bold transition-[transform,background-color,filter] active:scale-[0.97]'
  if (kept) {
    return <button type="button" onClick={() => act.remove.mutate(kept.id, { onSuccess: () => toast(`${title} is no longer kept. Files already downloaded stay.`) })} className={cn(base, 'border border-accent/60 bg-accent/15 text-fg hover:bg-accent/25', className)} title="Stop keeping this up to date"><Icon name="check" className="size-4" />Kept</button>
  }
  if (kind === 'movie') return <button type="button" disabled={act.add.isPending} onClick={() => add('all')} className={cn(base, 'border border-line-2 font-semibold hover:bg-fg/10 disabled:opacity-60', className)}><Icon name="download" className="size-4" />{label ?? 'Keep this'}</button>
  return (
    <span className="relative inline-block">
      <button type="button" aria-haspopup="menu" aria-expanded={menu} disabled={act.add.isPending} onClick={() => setMenu((m) => !m)} className={cn(base, 'border border-line-2 font-semibold hover:bg-fg/10 disabled:opacity-60', className)}><Icon name="download" className="size-4" />{label ?? 'Keep this'}</button>
      {menu ? (
        <div role="menu" className="absolute bottom-full left-0 z-20 mb-2 grid w-64 gap-1 rounded-2xl border border-line-2 bg-surface p-2 shadow-2xl">
          <button type="button" role="menuitem" onClick={() => add('all')} className="rounded-xl px-3 py-2 text-left text-sm hover:bg-fg/5"><b className="block">All episodes</b><span className="text-xs text-muted">Every episode that has aired, and each new one</span></button>
          <button type="button" role="menuitem" onClick={() => add('new')} className="rounded-xl px-3 py-2 text-left text-sm hover:bg-fg/5"><b className="block">New episodes only</b><span className="text-xs text-muted">Only episodes that air from now on</span></button>
        </div>
      ) : null}
    </span>
  )
}

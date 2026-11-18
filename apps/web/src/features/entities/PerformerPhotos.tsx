import { useInfiniteQuery } from '@tanstack/react-query'
import { useCallback, useMemo, useRef, useState } from 'react'
import { ApiError, api } from '@/api/client'
import { Button } from '@/design/Button'
import { PhotoViewer } from '@/features/photos/PhotoViewer'
import { photoName, photosUrl, type Photo, type PhotoPage } from '@/features/photos/photos'

const PER = 48
export const PHOTOS_EMPTY = 'None of your photos have been matched to this performer yet. Open Photos, People, find the people in your photos and name this one.'
export const photosHeading = (n: number, total: number) => (total ? `In your photos · ${total.toLocaleString('en-US')}${n < total ? ` (${n.toLocaleString('en-US')} shown)` : ''}` : 'In your photos')

/** The library photos in which this performer's face was recognised, a page at a time. */
export function usePerformerPhotos(id: number, name: string) {
  const q = useInfiniteQuery({
    queryKey: ['performer-photos', id] as const,
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const r = await api<PhotoPage>(photosUrl({ channels: [], q: '', sort: 'date_desc', favorites: false, favoriteIds: new Set(), page: pageParam, per: PER, face: { performerId: id, groupIds: [], groupId: null, label: name } }))
      if (!r || r.ok !== true) throw new ApiError(0, r?.detail || 'Could not load the photos.')
      return r
    },
    getNextPageParam: (last, all) => (all.reduce((n, p) => n + p.items.length, 0) < last.total && last.items.length ? all.length : undefined),
    staleTime: 30_000,
    retry: false,
  })
  const items = useMemo<Photo[]>(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data])
  const total = q.data?.pages[0]?.total ?? 0
  const more = useCallback(() => { if (q.hasNextPage && !q.isFetchingNextPage) void q.fetchNextPage() }, [q])
  return { q, items, total, more }
}

/** The photos of the library in which this performer's face was recognised (the People page names a face; this lists everything it found). Opens in the photo viewer. */
export function PerformerPhotos({ id, name }: { id: number; name: string }) {
  const { q, items, total, more } = usePerformerPhotos(id, name)
  const [open, setOpen] = useState<number | null>(null)
  const thumbs = useRef(new Map<number, HTMLElement>())

  if (q.isPending) return <p role="status" className="py-3 text-sm text-muted">Looking for {name} in your photos…</p>
  if (q.isError) return <p role="alert" className="py-3 text-sm text-warn">Your photos could not be searched. Try again later.</p>
  return (
    <section aria-label="In your photos" className="grid gap-3">
      <h3 className="text-sm font-semibold">{photosHeading(items.length, total)}</h3>
      {items.length ? (
        <>
          <ul className="grid grid-cols-3 gap-2 sm:grid-cols-4 lg:grid-cols-6 2xl:grid-cols-8">
            {items.map((p, i) => (
              <li key={`${p.channel_id}:${p.message_id}`} className="overflow-hidden rounded-xl border border-line bg-surface">
                <button type="button" ref={(el) => { if (el) thumbs.current.set(i, el); else thumbs.current.delete(i) }} onClick={() => setOpen(i)} aria-label={`Open photo: ${photoName(p)}`} className="block aspect-square w-full">
                  <img src={p.thumbnail_url} alt="" loading="lazy" draggable={false} className="size-full object-cover transition-transform duration-200 hover:scale-[1.03]" />
                </button>
              </li>
            ))}
          </ul>
          {q.hasNextPage ? <div className="flex justify-center"><Button disabled={q.isFetchingNextPage} onClick={more}>{q.isFetchingNextPage ? 'Loading…' : 'Show more photos'}</Button></div> : null}
        </>
      ) : <p className="py-2 text-sm text-muted">{PHOTOS_EMPTY}</p>}
      {open !== null && items[open] ? (
        <PhotoViewer items={items} index={open} total={total} onIndex={setOpen} onClose={() => setOpen(null)} onNeedMore={more} origin={(i) => thumbs.current.get(i)?.getBoundingClientRect() ?? null} />
      ) : null}
    </section>
  )
}

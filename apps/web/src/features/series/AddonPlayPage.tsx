import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { api } from '@/api/client'
import { Icon } from '@/design/Icon'
import { useRouter } from '@tanstack/react-router'
import { Player } from '@/features/watch/Player'
import { leaveFullscreen, useBackOnEscape } from '@/features/watch/fullscreen'
import { useSkipTimes } from '@/features/watch/skip'

export interface AddonPlay { src: string; name: string; title?: string; poster?: string; kind: 'movie' | 'series'; ident: string; season?: number; episode?: number }

/** A stream from one of the person's add-ons, played on a page of its own with the same player as the library's videos (the subtitle files the add-ons list are in its CC menu). */
export const ADDON_SAVE_EVERY_MS = 10_000
/** The last position is only worth offering when it is past the first moments and not nearly the end. */
export const worthResuming = (position: number) => Number.isFinite(position) && position >= 20

const progressUrl = (p: AddonPlay) => `/api/screen/addon-progress?${new URLSearchParams({ kind: p.kind, ident: p.ident, ...(p.season !== undefined ? { season: String(p.season) } : {}), ...(p.episode !== undefined ? { episode: String(p.episode) } : {}) })}`

export function AddonPlayPage({ play }: { play: AddonPlay }) {
  const router = useRouter()
  useBackOnEscape(() => router.history.back())
  useEffect(() => () => leaveFullscreen(), [])   // leaving the page leaves full screen
  const last = useRef(0)
  const known = useRef({ t: 0, d: 0 })
  // Where this title stopped before (the player waits for the answer, so it starts there instead of jumping after it began).
  const saved = useQuery({ queryKey: ['addon-progress', play.kind, play.ident, play.season ?? null, play.episode ?? null] as const, queryFn: () => api<{ position: number }>(progressUrl(play)), retry: false, staleTime: 0, gcTime: 0 })
  const skips = useSkipTimes(play.ident ? `/api/screen/addon-skip-times?${new URLSearchParams({ kind: play.kind, ident: play.ident, ...(play.season != null && play.episode != null ? { season: String(play.season), episode: String(play.episode) } : {}) })}` : null)
  const save = (position: number, duration: number, force = false) => {
    known.current = { t: position, d: duration }
    const now = Date.now()
    if (!force && now - last.current < ADDON_SAVE_EVERY_MS) return
    last.current = now
    if (!play.ident || !(duration > 0)) return
    void api('/api/screen/addon-progress', { method: 'POST', json: { kind: play.kind, ident: play.ident, season: play.season, episode: play.episode, name: play.name, title: play.title, poster: play.poster, position, duration, src: play.src } }).catch(() => { /* best-effort */ })
  }
  const token = play.src.split('/api/screen/stream/')[1]
  if (saved.isPending) return <div role="status" aria-label="Loading" className="grid place-items-center py-24"><span className="size-6 animate-spin rounded-full border-2 border-line border-t-accent" /></div>
  const startAt = worthResuming(saved.data?.position ?? 0) ? saved.data!.position : 0
  return (
    <div className="grid w-full gap-4 px-4 pb-10 pt-4 sm:px-10">
      <div className="flex items-center gap-3">
        <button type="button" onClick={() => router.history.back()} aria-label="Go back" title="Go back" className="grid size-10 shrink-0 place-items-center rounded-xl border border-line bg-surface hover:bg-surface-2"><Icon name="arrow-left" /></button>
        <h1 className="m-0 min-w-0 text-[clamp(16px,1.8vw,22px)] font-extrabold leading-tight tracking-tight [overflow-wrap:anywhere]">{play.name}</h1>
      </div>
      <Player onBack={() => router.history.back()} key={play.src} skips={skips} src={play.src} convert={{ url: (t) => `/api/screen/stream-compat/${token}?t=${t.toFixed(3)}`, info: `/api/screen/stream-compat/${token}/info` }} autoPlay startAt={startAt} onTime={(t, d) => save(t, d)} onEnd={() => save(known.current.d, known.current.d, true)} addon={{ kind: play.kind, ident: play.ident, season: play.season, episode: play.episode, filename: play.name }} />
    </div>
  )
}

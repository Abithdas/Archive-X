import { Icon } from '@/design/Icon'
import { useRouter } from '@tanstack/react-router'
import { Player } from '@/features/watch/Player'

export interface AddonPlay { src: string; name: string; kind: 'movie' | 'series'; ident: string; season?: number; episode?: number }

/** A stream from one of the person's add-ons, played on a page of its own with the same player as the library's videos (the subtitle files the add-ons list are in its CC menu). */
export function AddonPlayPage({ play }: { play: AddonPlay }) {
  const router = useRouter()
  return (
    <div className="grid w-full gap-4 px-4 pb-10 pt-4 sm:px-10">
      <div className="flex items-center gap-3">
        <button type="button" onClick={() => router.history.back()} aria-label="Go back" title="Go back" className="grid size-10 shrink-0 place-items-center rounded-xl border border-line bg-surface hover:bg-surface-2"><Icon name="arrow-left" /></button>
        <h1 className="m-0 min-w-0 text-[clamp(16px,1.8vw,22px)] font-extrabold leading-tight tracking-tight [overflow-wrap:anywhere]">{play.name}</h1>
      </div>
      <Player key={play.src} src={play.src} autoPlay addon={{ kind: play.kind, ident: play.ident, season: play.season, episode: play.episode, filename: play.name }} />
    </div>
  )
}

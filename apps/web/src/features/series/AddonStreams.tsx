import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '@/api/client'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'

interface StreamItem { addon: string; name: string; kind: 'link' | 'play' | 'blocked'; href?: string; play?: string; host?: string }
interface Streams { ok: boolean; items: StreamItem[]; skipped: number; addons: string[] }

/**
 * What the person's own add-ons offer for a title. A link opens where it points (YouTube, a service's page) in the browser; a stream from their own
 * sources (a media server at home, with the switch in Settings, Addons) or a public HTTPS host plays here; any other direct stream is listed and not played.
 * Torrents are never used.
 */
export function AddonStreams({ kind, ident, season, episode }: { kind: 'movie' | 'series'; ident: string; season?: number; episode?: number }) {
  const [playing, setPlaying] = useState<StreamItem | null>(null)
  const extra = kind === 'series' ? `?season=${season ?? ''}&episode=${episode ?? ''}` : ''
  const q = useQuery({ queryKey: ['addon-streams', kind, ident, season ?? null, episode ?? null] as const, queryFn: () => api<Streams>(`/api/screen/streams/${kind}/${ident}${extra}`), retry: false, staleTime: 5 * 60_000, enabled: /^tt\d+$/.test(ident) && (kind === 'movie' || (season !== undefined && episode !== undefined)) })
  const d = q.data
  if (!d || !Array.isArray(d.items) || !Array.isArray(d.addons) || (!d.items.length && !d.addons.length)) return null   // nothing, or an answer that is not ours: show nothing rather than break the page
  const groups = d.addons.length ? d.addons : [...new Set(d.items.map((i) => i.addon))]
  return (
    <section aria-label="From your add-ons" className="grid gap-2">
      <h3 className="text-sm font-bold">From your add-ons</h3>
      {!d.items.length ? <p className="text-xs text-muted">{d.addons.join(', ')} had nothing playable or openable for this title.</p> : null}
      {groups.map((name) => {
        const rows = d.items.filter((i) => i.addon === name)
        if (!rows.length) return null
        return (
          <div key={name} className="grid gap-1">
            <span className="text-[11px] font-bold uppercase tracking-wide text-faint">{name}</span>
            <ul aria-label={name} className="m-0 grid list-none gap-1 p-0">
              {rows.map((r, i) => (
                <li key={`${r.name}:${i}`} className="m-0">
                  {r.kind === 'link' ? <a href={r.href} target="_blank" rel="noreferrer" className="flex items-center justify-between gap-3 rounded-xl border border-line px-3 py-2 text-[13px] font-semibold transition-colors hover:bg-fg/10"><span className="min-w-0 truncate" title={r.name}>{r.name}</span><span aria-hidden="true" className="shrink-0 text-xs opacity-70">↗</span></a>
                    : r.kind === 'play' ? <button type="button" onClick={() => setPlaying(r)} className="flex w-full items-center justify-between gap-3 rounded-xl border border-line px-3 py-2 text-left text-[13px] font-semibold transition-colors hover:bg-fg/10"><span className="min-w-0 truncate" title={r.name}>{r.name}</span><span className="inline-flex shrink-0 items-center gap-1 text-xs"><Icon name="play" className="size-3.5" />Play</span></button>
                    : <p className="m-0 flex items-center justify-between gap-3 rounded-xl border border-dashed border-line px-3 py-2 text-[13px] text-muted"><span className="min-w-0 truncate" title={r.name}>{r.name}</span><span className="shrink-0 text-[11px]">Not played here{r.host ? ` (${r.host})` : ''}</span></p>}
                </li>
              ))}
            </ul>
          </div>
        )
      })}
      {d.skipped ? <p className="text-[11px] text-faint">{d.skipped} stream{d.skipped === 1 ? '' : 's'} of a kind this app does not use {d.skipped === 1 ? 'was' : 'were'} left out.</p> : null}
      {playing?.play ? (
        <Dialog open onClose={() => setPlaying(null)} title={playing.name} wide>
          <video src={playing.play} controls autoPlay playsInline className="aspect-video w-full rounded-xl bg-black" />
        </Dialog>
      ) : null}
    </section>
  )
}

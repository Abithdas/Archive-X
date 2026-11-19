import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'

interface StreamItem {
  addon: string
  name: string
  kind: 'link' | 'play' | 'blocked'
  href?: string
  play?: string
  host?: string
  height?: number
  quality?: string
  source?: string
  hdr?: string[]
  languages?: string[]
}
interface Streams { ok: boolean; items: StreamItem[]; skipped: number; addons: string[] }
type StreamSort = 'addon' | 'quality'
type HdrFilter = 'all' | 'hdr' | 'dv' | 'none'

function qualityLabel(height: number): string {
  if (height >= 2000) return '4K'
  if (height >= 1000) return '1080p'
  if (height >= 700) return '720p'
  return height ? height + 'p' : ''
}

/**
 * What the person's add-ons offer for a title. Links open elsewhere; eligible direct streams play here.
 * Add-on, quality, language, source and HDR/DV filters narrow the streams returned for this title.
 * Torrents are never used.
 */
export function AddonStreams({ kind, ident, season, episode }: { kind: 'movie' | 'series'; ident: string; season?: number; episode?: number }) {
  const [playing, setPlaying] = useState<StreamItem | null>(null)
  const [selectedAddon, setSelectedAddon] = useState('')
  const [sort, setSort] = useState<StreamSort>('addon')
  const [quality, setQuality] = useState('')
  const [language, setLanguage] = useState('')
  const [source, setSource] = useState('')
  const [hdr, setHdr] = useState<HdrFilter>('all')
  const extra = kind === 'series' ? '?season=' + (season ?? '') + '&episode=' + (episode ?? '') : ''
  const q = useQuery({ queryKey: ['addon-streams', kind, ident, season ?? null, episode ?? null] as const, queryFn: () => api<Streams>('/api/screen/streams/' + kind + '/' + ident + extra), retry: false, staleTime: 5 * 60_000, enabled: /^tt\d+$/.test(ident) && (kind === 'movie' || (season !== undefined && episode !== undefined)) })
  const d = q.data
  useEffect(() => {
    setPlaying(null)
    setSelectedAddon('')
    setSort('addon')
    setQuality('')
    setLanguage('')
    setSource('')
    setHdr('all')
  }, [kind, ident, season, episode])
  if (!d || !Array.isArray(d.items) || !Array.isArray(d.addons) || (!d.items.length && !d.addons.length)) return null

  const addonNames = d.addons.length ? d.addons : [...new Set(d.items.map((item) => item.addon))]
  const qualityOptions = [...new Set(d.items.map((item) => item.height ?? 0).filter((height) => height > 0))].sort((a, b) => b - a)
  const languageOptions = [...new Set(d.items.flatMap((item) => item.languages ?? []))].sort((a, b) => a.localeCompare(b))
  const sourceOptions = [...new Set(d.items.map((item) => item.source ?? '').filter(Boolean))].sort((a, b) => a.localeCompare(b))
  const hdrOptions = [...new Set(d.items.flatMap((item) => item.hdr ?? []))]
  const filtered = d.items.map((item, index) => ({ item, index })).filter(({ item }) =>
    (!selectedAddon || item.addon === selectedAddon)
    && (!quality || (item.height ?? 0) === Number(quality))
    && (!language || (item.languages ?? []).includes(language))
    && (!source || item.source === source)
    && (hdr === 'all' || (hdr === 'hdr' && (item.hdr ?? []).includes('HDR')) || (hdr === 'dv' && (item.hdr ?? []).includes('DV')) || (hdr === 'none' && !(item.hdr ?? []).length)),
  )
  const rows = sort === 'quality'
    ? filtered.sort((a, b) => (b.item.height ?? 0) - (a.item.height ?? 0) || a.index - b.index)
    : filtered.sort((a, b) => a.index - b.index)
  const visibleAddons = addonNames.filter((name) => rows.some(({ item }) => item.addon === name))
  const selectClass = 'h-8 max-w-full appearance-none rounded-full border border-line bg-surface-2 px-3 pr-7 text-[11px] font-semibold text-fg outline-none transition-colors hover:bg-fg/10 focus-visible:ring-2 focus-visible:ring-accent'
  const countLabel = rows.length + ' stream' + (rows.length === 1 ? '' : 's')
  return (
    <section aria-label="From your add-ons" className="grid gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-bold">From your add-ons</h3>
        <span aria-live="polite" className="text-[11px] text-muted">{countLabel}</span>
      </div>
      {d.items.length ? (
        <>
          <div role="tablist" aria-label="Stream add-ons" className="flex w-fit max-w-full gap-1 overflow-x-auto rounded-full border border-line bg-fg/5 p-1">
            <button type="button" role="tab" aria-selected={!selectedAddon} onClick={() => setSelectedAddon('')} className={'shrink-0 rounded-full px-3 py-1.5 text-[11px] font-bold transition-colors ' + (!selectedAddon ? 'bg-fg text-bg' : 'text-muted hover:text-fg')}>All <span className="ml-1 opacity-70">{d.items.length}</span></button>
            {addonNames.map((name) => {
              const active = selectedAddon === name
              const count = d.items.filter((item) => item.addon === name).length
              return <button key={name} type="button" role="tab" aria-selected={active} onClick={() => setSelectedAddon(name)} className={'shrink-0 rounded-full px-3 py-1.5 text-[11px] font-bold transition-colors ' + (active ? 'bg-fg text-bg' : 'text-muted hover:text-fg')}>{name} <span className="ml-1 opacity-70">{count}</span></button>
            })}
          </div>
          <div aria-label="Stream filters" className="flex flex-wrap items-center gap-1.5">
            <label><span className="sr-only">Addon order</span><select aria-label="Addon order" className={selectClass} value={sort} onChange={(e) => setSort(e.target.value as StreamSort)}><option value="addon">↕ Addon order</option><option value="quality">Best quality</option></select></label>
            {qualityOptions.length > 1 ? <label><span className="sr-only">Quality</span><select aria-label="Quality" className={selectClass} value={quality} onChange={(e) => setQuality(e.target.value)}><option value="">Quality</option>{qualityOptions.map((height) => <option key={height} value={String(height)}>{qualityLabel(height)}</option>)}</select></label> : null}
            {languageOptions.length > 1 ? <label><span className="sr-only">Language</span><select aria-label="Language" className={selectClass} value={language} onChange={(e) => setLanguage(e.target.value)}><option value="">Language</option>{languageOptions.map((value) => <option key={value} value={value}>{value}</option>)}</select></label> : null}
            {sourceOptions.length > 1 ? <label><span className="sr-only">Source</span><select aria-label="Source" className={selectClass} value={source} onChange={(e) => setSource(e.target.value)}><option value="">Source</option>{sourceOptions.map((value) => <option key={value} value={value}>{value}</option>)}</select></label> : null}
            {hdrOptions.length ? <label><span className="sr-only">HDR / DV</span><select aria-label="HDR / DV" className={selectClass} value={hdr} onChange={(e) => setHdr(e.target.value as HdrFilter)}><option value="all">HDR / DV</option>{hdrOptions.includes('HDR') ? <option value="hdr">HDR</option> : null}{hdrOptions.includes('DV') ? <option value="dv">Dolby Vision</option> : null}<option value="none">No HDR / DV</option></select></label> : null}
          </div>
          {!rows.length ? <p className="m-0 rounded-xl border border-dashed border-line px-3 py-3 text-xs text-muted">Nothing matches those filters.</p> : visibleAddons.map((name) => (
            <ul key={name} aria-label={name} className="m-0 grid list-none gap-1 p-0">
              {rows.filter(({ item }) => item.addon === name).map(({ item, index }) => (
                <li key={name + ':' + item.name + ':' + index} className="m-0">
                  <div className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-3 rounded-xl border border-line px-3 py-2">
                    <div className="grid min-w-14 place-items-center rounded-lg bg-fg/5 px-2 py-2 text-center">
                      <span className="text-[13px] font-black">{qualityLabel(item.height ?? 0) || '—'}</span>
                      {(item.hdr ?? []).length ? <span className="text-[9px] font-bold uppercase text-muted">{(item.hdr ?? []).includes('DV') ? 'Dolby Vision' : 'HDR'}</span> : null}
                    </div>
                    <div className="min-w-0">
                      {item.kind === 'link' ? <a href={item.href} target="_blank" rel="noreferrer" className="block truncate text-[13px] font-semibold hover:underline" title={item.name}>{item.name}<span aria-hidden="true" className="ml-1 opacity-60">↗</span></a>
                        : item.kind === 'play' ? <button type="button" onClick={() => setPlaying(item)} className="block w-full truncate text-left text-[13px] font-semibold hover:underline" title={item.name}>{item.name}</button>
                        : <p className="m-0 truncate text-[13px] font-semibold text-muted" title={item.name}>{item.name}</p>}
                      <p className="m-0 mt-0.5 truncate text-[10px] text-muted">
                        {[item.addon, item.source, (item.languages ?? []).join(', ')].filter(Boolean).join(' · ')}
                        {item.kind === 'play' ? <span className="ml-2 inline-flex items-center gap-1 text-[10px] font-semibold text-accent"><Icon name="play" className="size-3" />Play</span> : item.kind === 'blocked' && item.host ? <span className="ml-2">Not played here ({item.host})</span> : null}
                      </p>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          ))
          }
          {d.skipped ? <p className="text-[11px] text-faint">{d.skipped} stream{d.skipped === 1 ? '' : 's'} of a kind this app does not use {d.skipped === 1 ? 'was' : 'were'} left out.</p> : null}
        </>
      ) : <p className="text-xs text-muted">{d.addons.join(', ')} had nothing playable or openable for this title.</p>}
      {playing?.play ? (
        <Dialog open onClose={() => setPlaying(null)} title={playing.name} wide>
          <video src={playing.play} controls autoPlay playsInline className="aspect-video w-full rounded-xl bg-black" />
        </Dialog>
      ) : null}
    </section>
  )
}

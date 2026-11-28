import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import { Link, useNavigate } from '@tanstack/react-router'
import { toast } from 'sonner'
import { Icon } from '@/design/Icon'
import { errorText } from '@/features/content/useContentMode'
import { DownloadsDialog, downloadsOf } from '@/features/collection/DownloadsDialog'
import { collectionKey, useCollection } from '@/features/collection/useCollection'
import { StreamingActions } from '@/features/settings/StreamingSetup'

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
  bingeGroup?: string
  /** A torrent (played through the Stremio streaming server), with what the add-on's text says about it. */
  torrent?: boolean
  seeders?: number
  size?: number
  provider?: string
  /** A debrid service already has it: it starts at once. */
  cached?: boolean
  /** The release's file name, as the add-on writes it on the first line of its description. */
  release?: string
}
interface Streams { ok: boolean; items: StreamItem[]; skipped: number; addons: string[]; torrents?: boolean; streaming_server?: { url: string; up: boolean } | null }
type StreamSort = 'addon' | 'quality' | 'seeds' | 'largest' | 'smallest'
type HdrFilter = 'all' | 'hdr' | 'dv' | 'none'

const bingeKey = (ident: string) => `archive-x:binge:${ident}`
/** The label (Stremio's "bingeGroup") of the stream played last for this show, so the next episode offers the same kind first. */
const lastBinge = (ident: string): string => { try { return localStorage.getItem(bingeKey(ident)) ?? '' } catch { return '' } }
const rememberBinge = (ident: string, group: string) => { try { localStorage.setItem(bingeKey(ident), group) } catch { /* private mode */ } }

const PREFS_KEY = 'archive-x:streams-prefs'
interface Prefs { sort: StreamSort; language: string; source: string; hdr: HdrFilter; instant: boolean }
const SORTS: readonly StreamSort[] = ['addon', 'quality', 'seeds', 'largest', 'smallest']
const HDRS: readonly HdrFilter[] = ['all', 'hdr', 'dv', 'none']
/** How the person last sorted and filtered streams: kept, so every title opens the way they like (quality and add-on are per title and are not). */
export function readStreamPrefs(): Prefs {
  const blank: Prefs = { sort: 'addon', language: '', source: '', hdr: 'all', instant: false }
  try {
    const v = JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}') as Partial<Prefs>
    return {
      sort: SORTS.includes(v.sort as StreamSort) ? (v.sort as StreamSort) : blank.sort,
      language: typeof v.language === 'string' ? v.language.slice(0, 40) : '',
      source: typeof v.source === 'string' ? v.source.slice(0, 40) : '',
      hdr: HDRS.includes(v.hdr as HdrFilter) ? (v.hdr as HdrFilter) : 'all',
      instant: v.instant === true,
    }
  } catch { return blank }
}
const saveStreamPrefs = (p: Prefs) => { try { localStorage.setItem(PREFS_KEY, JSON.stringify(p)) } catch { /* private mode */ } }

export function sizeText(bytes: number): string {
  if (!(bytes > 0)) return ''
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)))
  const v = bytes / 1024 ** i
  return `${v >= 100 || i < 2 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

/** What kind of release a stream is, as a short line ("Web · HEVC"), from what the add-on says about it. Empty when nothing is known: the row then shows the add-on's own text. */
export function typeLine(item: Pick<StreamItem, 'source' | 'release' | 'name'>): string {
  const text = `${item.release ?? ''} ${item.name}`
  const source = [[/remux/i, 'Remux'], [/blu-?ray|bdrip|brrip/i, 'BluRay'], [/web/i, 'Web'], [/hdtv|tvrip/i, 'HDTV'], [/dvd/i, 'DVD'], [/\bcam\b|telesync|\bts\b/i, 'CAM']] as const
  const kind = source.find(([re]) => re.test(item.source ?? '') || re.test(text))?.[1] ?? ''
  const codec = /hevc|x\.?265|h\.?265/i.test(text) ? 'HEVC' : /\bav1\b/i.test(text) ? 'AV1' : ''
  return [kind, codec].filter(Boolean).join(' · ')
}

/** The row's first line: the kind of release when it is known (the file name then has a line of its own below), else the add-on's own text. */
const headline = (item: StreamItem): string => (item.release ? typeLine(item) : '') || item.name

function qualityLabel(height: number): string {
  if (height >= 2000) return '4K'
  if (height >= 1000) return '1080p'
  if (height >= 700) return '720p'
  return height ? height + 'p' : ''
}

function seedsOf(text: string): number {
  const match = text.match(/\b(\d[\d,]*)\s*(?:seeds?|seeders?)\b/i) ?? text.match(/\b(?:seeds?|seeders?)\s*[:=]\s*(\d[\d,]*)\b/i)
  return match ? Number(match[1]!.replace(/,/g, '')) : -1
}

function sizeOf(text: string): number {
  const match = text.match(/\b(\d+(?:[.,]\d+)?)\s*(TiB|GiB|MiB|KiB|TB|GB|MB|KB)\b/i)
  if (!match) return -1
  const value = Number(match[1]!.replace(',', '.'))
  const unit = match[2]!.toLowerCase()
  const power = unit.startsWith('t') ? 4 : unit.startsWith('g') ? 3 : unit.startsWith('m') ? 2 : 1
  return value * (unit.endsWith('ib') ? 1024 : 1000) ** power
}

function compareKnown(a: number, b: number, descending: boolean): number {
  if (a < 0 || b < 0) return a < 0 ? (b < 0 ? 0 : 1) : -1
  return descending ? b - a : a - b
}

/**
 * What the person's add-ons offer for a title. Links open elsewhere; eligible direct streams play here.
 * Add-on, quality, language, source and HDR/DV filters narrow the streams returned for this title.
 * A torrent plays through the person's Stremio streaming server (Settings, Addons); without it the torrent is listed and says what is missing.
 */
/** Shown while torrents are listed but Stremio's streaming server is not running. */
function StartServer({ onUp }: { onUp: () => void }) {
  return (
    <div role="status" className="mb-2 grid gap-1.5 rounded-xl border border-warn/40 bg-warn/10 p-2.5 text-xs">
      <p className="m-0 font-semibold">Torrents need Stremio&apos;s streaming server, which is not running.</p>
      <StreamingActions onUp={onUp} />
    </div>
  )
}

export function AddonStreams({ kind, ident, season, episode }: { kind: 'movie' | 'series'; ident: string; season?: number; episode?: number }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [group, setGroup] = useState(() => (kind === 'series' ? lastBinge(ident) : ''))
  const [selectedAddon, setSelectedAddon] = useState('')
  const [prefs, setPrefs] = useState<Prefs>(readStreamPrefs)
  const { sort, language, source, hdr, instant } = prefs
  const change = (patch: Partial<Prefs>) => setPrefs((p) => { const next = { ...p, ...patch }; saveStreamPrefs(next); return next })
  const setSort = (v: StreamSort) => change({ sort: v })
  const setLanguage = (v: string) => change({ language: v })
  const setSource = (v: string) => change({ source: v })
  const setHdr = (v: HdrFilter) => change({ hdr: v })
  const [quality, setQuality] = useState('')
  // Download: one stream saved to this computer (dx.bi's download icon on a stream), with the Downloads window showing how it goes.
  const [downloads, setDownloads] = useState(false)
  const download = useMutation({
    mutationFn: (item: StreamItem) => api('/api/screen/stream-download', { method: 'POST', json: { play: item.play, kind, ident, season, episode, name: item.name } }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: collectionKey }); setDownloads(true) },
    onError: (e) => toast.error(errorText(e, 'Could not start the download.')),
  })
  const open = (item: StreamItem) => {
    if (!item.play) return
    if (kind === 'series' && item.bingeGroup) { rememberBinge(ident, item.bingeGroup); setGroup(item.bingeGroup) }
    void navigate({ to: '/addon-play', search: { src: item.play, name: item.name, kind, ident, season, episode } })
  }
  const extra = kind === 'series' ? '?season=' + (season ?? '') + '&episode=' + (episode ?? '') : ''
  const q = useQuery({ queryKey: ['addon-streams', kind, ident, season ?? null, episode ?? null] as const, queryFn: () => api<Streams>('/api/screen/streams/' + kind + '/' + ident + extra), retry: false, staleTime: 5 * 60_000, enabled: /^tt\d+$/.test(ident) && (kind === 'movie' || (season !== undefined && episode !== undefined)) })
  const d = q.data
  const saved = useCollection(Boolean(d?.items?.length))   // the count on the Downloads button; asked only when there are streams to download
  const downloadCount = downloadsOf(saved.data?.titles ?? []).reduce((n, g) => n + g.items.length, 0)
  useEffect(() => {
    setSelectedAddon('')
    setQuality('')
    setGroup(kind === 'series' ? lastBinge(ident) : '')
  }, [kind, ident, season, episode])
  if (!d || !Array.isArray(d.items) || !Array.isArray(d.addons) || (!d.items.length && !d.addons.length)) return null

  const addonNames = [...new Set(d.addons.length ? d.addons : d.items.map((item) => item.addon))]
  const qualityOptions = [...new Set(d.items.map((item) => item.height ?? 0).filter((height) => height > 0))].sort((a, b) => b - a)
  const languageOptions = [...new Set(d.items.flatMap((item) => item.languages ?? []))].sort((a, b) => a.localeCompare(b))
  const sourceOptions = [...new Set(d.items.map((item) => item.source ?? '').filter(Boolean))].sort((a, b) => a.localeCompare(b))
  const hdrOptions = [...new Set(d.items.flatMap((item) => item.hdr ?? []))]
  const filtered = d.items.map((item, index) => ({ item, index, seeds: item.seeders ?? seedsOf(item.name), size: item.size ?? sizeOf(item.name) })).filter(({ item }) =>
    (!selectedAddon || item.addon === selectedAddon)
    && (!instant || item.cached)
    && (!quality || (item.height ?? 0) === Number(quality))
    && (!language || (item.languages ?? []).includes(language))
    && (!source || item.source === source)
    && (hdr === 'all' || (hdr === 'hdr' && (item.hdr ?? []).includes('HDR')) || (hdr === 'dv' && (item.hdr ?? []).includes('DV')) || (hdr === 'none' && !(item.hdr ?? []).length)),
  )
  const rows = filtered.sort((a, b) => {
    const order = sort === 'quality' ? compareKnown(a.item.height ?? 0, b.item.height ?? 0, true)
      : sort === 'seeds' ? compareKnown(a.seeds, b.seeds, true)
        : sort === 'largest' ? compareKnown(a.size, b.size, true)
          : sort === 'smallest' ? compareKnown(a.size, b.size, false)
            : 0
    const same = (x: typeof a) => (group && x.item.bingeGroup === group ? 1 : 0)
    return same(b) - same(a) || order || a.index - b.index
  })
  const visibleAddons = addonNames.filter((name) => rows.some(({ item }) => item.addon === name))
  // a filter that is set turns white, so what narrows the list is plain to see (the sort order is not a filter)
  const pill = (active: boolean) => 'h-8 max-w-full appearance-none rounded-full border px-3 pr-7 text-[11px] font-semibold outline-none transition-colors focus-visible:ring-2 focus-visible:ring-accent ' + (active ? 'border-white bg-white text-black' : 'border-line bg-surface-2 text-fg hover:bg-fg/10')
  const selectClass = pill(false)
  const filtering = Boolean(selectedAddon || quality || language || source || hdr !== 'all' || instant)
  const reset = () => { setSelectedAddon(''); setQuality(''); change({ language: '', source: '', hdr: 'all', instant: false }) }
  const countLabel = rows.length + ' stream' + (rows.length === 1 ? '' : 's')
  return (
    <section aria-label="From your add-ons" className="grid gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-bold">From your add-ons</h3>
        <div className="flex items-center gap-3">
          <button type="button" onClick={() => setDownloads(true)} className="inline-flex h-7 items-center gap-1.5 rounded-full border border-line px-2.5 text-[11px] font-semibold text-muted hover:text-fg"><Icon name="download" className="size-3.5" />Downloads{downloadCount ? ` (${downloadCount})` : ''}</button>
          <span aria-live="polite" className="text-[11px] text-muted">{countLabel}</span>
          {filtering ? <button type="button" onClick={reset} className="text-[11px] font-semibold text-fg underline">Reset</button> : null}
        </div>
      </div>
      <DownloadsDialog open={downloads} onClose={() => setDownloads(false)} />
      {d.items.length ? (
        <>
          {d.torrents && d.streaming_server && !d.streaming_server.up ? <StartServer onUp={() => void qc.invalidateQueries({ queryKey: ['addon-streams'] })} /> : null}
          <div role="tablist" aria-label="Stream add-ons" className="flex w-fit max-w-full flex-wrap gap-1 rounded-[20px] border border-line bg-fg/5 p-1">
            <button type="button" role="tab" aria-selected={!selectedAddon} onClick={() => setSelectedAddon('')} className={'shrink-0 rounded-full px-3 py-1.5 text-[11px] font-bold transition-colors ' + (!selectedAddon ? 'bg-fg text-bg' : 'text-muted hover:text-fg')}>All <span className="ml-1 opacity-70">{d.items.length}</span></button>
            {addonNames.filter((name) => d.items.some((item) => item.addon === name)).map((name) => {   // an add-on with nothing for this title has no tab
              const active = selectedAddon === name
              const count = d.items.filter((item) => item.addon === name).length
              return <button key={name} type="button" role="tab" aria-selected={active} onClick={() => setSelectedAddon(name)} className={'max-w-full truncate rounded-full px-3 py-1.5 text-[11px] font-bold transition-colors ' + (active ? 'bg-fg text-bg' : 'text-muted hover:text-fg')}>{name} <span className="ml-1 opacity-70">{count}</span></button>
            })}
          </div>
          <div aria-label="Stream filters" className="flex flex-wrap items-center gap-1.5">
            <label><span className="sr-only">Addon order</span><select aria-label="Addon order" className={selectClass} value={sort} onChange={(e) => setSort(e.target.value as StreamSort)}><option value="addon">↕ Addon order</option><option value="quality">Best quality</option><option value="seeds">Most seeds</option><option value="largest">Largest first</option><option value="smallest">Smallest first</option></select></label>
            {qualityOptions.length > 1 ? <label><span className="sr-only">Quality</span><select aria-label="Quality" className={pill(Boolean(quality))} value={quality} onChange={(e) => setQuality(e.target.value)}><option value="">Quality</option>{qualityOptions.map((height) => <option key={height} value={String(height)}>{qualityLabel(height)}</option>)}</select></label> : null}
            {languageOptions.length > 1 ? <label><span className="sr-only">Language</span><select aria-label="Language" className={pill(Boolean(language))} value={language} onChange={(e) => setLanguage(e.target.value)}><option value="">Language</option>{languageOptions.map((value) => <option key={value} value={value}>{value}</option>)}</select></label> : null}
            {sourceOptions.length > 1 ? <label><span className="sr-only">Source</span><select aria-label="Source" className={pill(Boolean(source))} value={source} onChange={(e) => setSource(e.target.value)}><option value="">Source</option>{sourceOptions.map((value) => <option key={value} value={value}>{value}</option>)}</select></label> : null}
            {hdrOptions.length ? <label><span className="sr-only">HDR / DV</span><select aria-label="HDR / DV" className={pill(hdr !== 'all')} value={hdr} onChange={(e) => setHdr(e.target.value as HdrFilter)}><option value="all">HDR / DV</option>{hdrOptions.includes('HDR') ? <option value="hdr">HDR</option> : null}{hdrOptions.includes('DV') ? <option value="dv">Dolby Vision</option> : null}<option value="none">No HDR / DV</option></select></label> : null}
            {d.items.some((item) => item.cached) ? <button type="button" aria-pressed={instant} onClick={() => change({ instant: !instant })} className={'h-8 rounded-full border px-3 text-[11px] font-semibold transition-colors ' + (instant ? 'border-accent/60 bg-accent/15 text-fg' : 'border-line bg-surface-2 text-muted hover:text-fg')}>Instant only</button> : null}
          </div>
          {!rows.length ? <p className="m-0 rounded-xl border border-dashed border-line px-3 py-3 text-xs text-muted">Nothing matches those filters.</p> : visibleAddons.map((name) => (
            <ul key={name} aria-label={name} className="m-0 grid list-none gap-1 p-0">
              {rows.filter(({ item }) => item.addon === name).map(({ item, index }) => (
                <li key={name + ':' + item.name + ':' + index} className="m-0">
                  <div onClick={item.kind === 'play' ? () => open(item) : undefined} className={'group/row grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-4 border-b border-line/70 px-2 py-3.5' + (item.kind === 'play' ? ' cursor-pointer transition-colors hover:bg-fg/[0.07]' : '')}>
                    <div className="grid h-14 w-[88px] place-items-center rounded-lg bg-fg/10 px-2 text-center transition-colors group-hover/row:bg-accent group-hover/row:text-accent-fg">
                      <span className="text-[17px] font-black tracking-tight">{qualityLabel(item.height ?? 0) || '—'}</span>
                      {(item.hdr ?? []).length ? <span className="text-[9px] font-bold uppercase text-muted">{(item.hdr ?? []).includes('DV') ? 'Dolby Vision' : 'HDR'}</span> : null}
                    </div>
                    <div className="min-w-0">
                      {item.kind === 'link' ? <a href={item.href} target="_blank" rel="noreferrer" className="line-clamp-2 block break-words text-[14px] font-extrabold hover:underline" title={item.name}>{headline(item)}<span aria-hidden="true" className="ml-1 opacity-60">↗</span></a>
                        : item.kind === 'play' ? <button type="button" onClick={() => open(item)} aria-label={item.name} className="line-clamp-2 block w-full break-words text-left text-[14px] font-extrabold hover:underline" title={item.name}>{headline(item)}</button>
                        : <p className="m-0 line-clamp-2 break-words text-[14px] font-extrabold text-muted" title={item.name}>{headline(item)}</p>}
                      <p className="m-0 mt-1 flex flex-wrap items-center gap-x-1 text-xs leading-snug text-fg/70">
                        {[item.addon, item.provider, headline(item) === item.name ? item.source : '', (item.languages ?? []).join(', '), item.seeders != null ? `${item.seeders} seeds` : '', sizeText(item.size ?? 0)].filter(Boolean).join(' · ')}
                        {item.cached ? <span className="ml-2 rounded-full bg-fg/10 px-1.5 py-px text-[10px] font-bold">Instant</span> : null}
                        {group && item.bingeGroup === group ? <span className="ml-2 rounded-full bg-accent/15 px-1.5 py-px text-[10px] font-bold text-accent">Same as last episode</span> : null}
                        {item.kind === 'play' && headline(item) === item.name ? <span className="ml-2 inline-flex items-center gap-1 text-[10px] font-semibold text-accent"><Icon name="play" className="size-3" />Play</span> : item.kind === 'play' ? null : item.kind === 'blocked' && item.torrent ? <span className="ml-2">A torrent: it needs Stremio's streaming server, which is not running{d.streaming_server ? ` at ${d.streaming_server.url}` : ''}. <Link to="/settings" onClick={() => { try { localStorage.setItem('ax_next_settings_panel', 'addons') } catch { /* private mode */ } }} className="font-semibold text-accent underline">Set it up</Link></span> : item.kind === 'blocked' && item.host ? <span className="ml-2">Not played here ({item.host})</span> : null}
                      </p>
                      {item.release && headline(item) !== item.name ? <p className="m-0 mt-0.5 truncate text-[11px] text-faint" title={item.release}>{item.release}</p> : null}
                    </div>
                    {item.torrent && item.kind === 'play' ? (
                      <button type="button" aria-label={`Download ${item.name}`} title="Download" disabled={download.isPending}
                        onClick={(e) => { e.stopPropagation(); download.mutate(item) }}
                        className="grid size-9 place-items-center rounded-full bg-fg/10 text-fg opacity-0 transition-opacity hover:bg-fg/20 focus-visible:opacity-100 group-hover/row:opacity-100 disabled:opacity-40 [@media(hover:none)]:opacity-100"><Icon name="download" className="size-4" /></button>
                    ) : <span aria-hidden="true" />}
                  </div>
                </li>
              ))}
            </ul>
          ))
          }
          {d.skipped ? <p className="text-[11px] text-faint">{d.skipped} stream{d.skipped === 1 ? '' : 's'} of a kind this app does not use {d.skipped === 1 ? 'was' : 'were'} left out.</p> : null}
        </>
      ) : <p className="text-xs text-muted">{d.addons.join(', ')} had nothing playable or openable for this title.</p>}
    </section>
  )
}

interface ExtraItem { addon: string; text: string; rating: string; links: { name: string; category: string; url: string }[] }

/** What the person's add-ons that answer for details say about a title (ratings, production notes, post-credit scenes): text and links, nothing run. */
export function AddonExtras({ kind, ident }: { kind: 'movie' | 'series'; ident: string }) {
  const q = useQuery({ queryKey: ['addon-meta', kind, ident] as const, queryFn: () => api<{ items: ExtraItem[] }>(`/api/screen/addon-meta/${kind}/${ident}`), staleTime: 10 * 60_000, retry: false })
  const items = q.data?.items ?? []
  if (!items.length) return null
  return (
    <section aria-label="More from your add-ons" className="grid gap-3">
      <h3 className="px-1 text-sm font-bold">More from your add-ons</h3>
      {items.map((it) => (
        <article key={it.addon} className="grid gap-2 rounded-2xl border border-line p-3">
          <h4 className="m-0 flex items-baseline justify-between gap-2 text-[13px] font-bold"><span>{it.addon}</span>{it.rating ? <span className="text-xs font-semibold text-muted">{it.rating} on IMDb</span> : null}</h4>
          {it.text ? <p className="m-0 line-clamp-[12] whitespace-pre-line text-[13px] leading-relaxed text-fg/85">{it.text}</p> : null}
          {it.links.length ? <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">{it.links.map((l) => <li key={l.url} className="m-0"><a href={l.url} target="_blank" rel="noreferrer" className="inline-flex h-8 items-center rounded-full border border-line px-3 text-xs font-semibold hover:bg-fg/5">{l.name} ↗</a></li>)}</ul> : null}
        </article>
      ))}
    </section>
  )
}

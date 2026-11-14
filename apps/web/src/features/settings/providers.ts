/** Metadata panel rules: provider keys, the daily scan window, and the provider status cards. Mirrors the classic page. */
type Obj = Record<string, unknown>
const s = (v: unknown) => (v == null ? '' : String(v))
const n = (v: unknown) => Number(v) || 0

export type KeyKind = 'tpdb' | 'stash'
export const KEYS: Record<KeyKind, { label: string; field: 'tpdb_token' | 'stashdb_api_key'; noun: 'token' | 'key'; check: string }> = {
  tpdb: { label: 'ThePornDB — API Token', field: 'tpdb_token', noun: 'token', check: '/api/settings/check-tpdb' },
  stash: { label: 'StashDB — API Key', field: 'stashdb_api_key', noun: 'key', check: '/api/settings/check-stashdb' },
}

/** Only fields that were typed are sent; blank keeps the saved value. */
export function keysBody(tpdb: string, stash: string): Record<string, string> {
  const b: Record<string, string> = {}
  if (tpdb.trim()) b.tpdb_token = tpdb.trim()
  if (stash.trim()) b.stashdb_api_key = stash.trim()
  return b
}
export const keyPlaceholder = (kind: KeyKind, masked: unknown) =>
  masked ? `Saved ${KEYS[kind].noun} ${s(masked)} — leave blank to keep` : `saved ${KEYS[kind].noun} — leave blank to keep`

export interface ScanWindow { enabled: boolean; start: string; end: string }
export const DEFAULT_WINDOW: ScanWindow = { enabled: false, start: '01:00', end: '07:00' }
export const windowFrom = (r: Obj | undefined): ScanWindow => ({ enabled: Boolean(r?.enabled), start: s(r?.start) || '01:00', end: s(r?.end) || '07:00' })
const CLOCK = /^(?:[01]\d|2[0-3]):[0-5]\d$/
export const windowProblem = (w: ScanWindow) => (CLOCK.test(w.start) && CLOCK.test(w.end) ? '' : 'Enter the start and end times as HH:MM.')
export const windowBody = (w: ScanWindow) => ({ enabled: w.enabled, start: w.start || '01:00', end: w.end || '07:00' })
export const windowStatus = (r: ScanWindow) => (r.enabled ? `Enabled · ${r.start}–${r.end}` : 'Disabled')
export const windowSaved = (r: ScanWindow) => (r.enabled ? `Saved · ${r.start}–${r.end}` : 'Saved · window disabled')

export interface ProviderCard { key: string; title: string; value: string; note: string; error?: string }
export function providerCards(r: Obj | undefined): ProviderCard[] {
  if (!r) return []
  const cards: ProviderCard[] = []
  for (const [i, p] of ((Array.isArray(r.providers) ? r.providers : []) as Obj[]).entries()) {
    cards.push({
      key: `p${i}`, title: `${(s(p.provider) || 'provider').toUpperCase()} · ${s(p.entity_type) || 'scene'}`, value: s(p.status) || 'IDLE',
      note: `Last sync: ${s(p.last_sync_at) || 'Never'} · Requests: ${n(p.request_count)}`, error: s(p.error) || undefined,
    })
  }
  for (const [k, raw] of Object.entries((r.broker ?? {}) as Record<string, Obj>)) {
    const p = raw ?? {}
    cards.push({
      key: `b${k}`, title: `${k === 'tpdb' ? 'THEPORNDB' : 'STASHDB'} · API broker`, value: `${n(p.active_slots)}/${n(p.max_concurrency)} active`,
      note: `Requests: ${n(p.requests)} · Deduplicated: ${n(p.deduplicated)} · Retries: ${n(p.retries)} · Cooldown: ${n(p.cooldown_seconds).toFixed(1)}s`,
    })
  }
  cards.push({ key: 'mc', title: 'Local metadata cache', value: n(r.metadata_cache_entries).toLocaleString(), note: 'Provider responses kept locally to avoid repeat lookups.' })
  cards.push({ key: 'ac', title: 'Artwork cache', value: n(r.artwork_cache_entries).toLocaleString(), note: 'Performer and studio images are served locally when cached.' })
  return cards
}
/** "1–3 of 7" for the cards in view (the classic rail shows at most four). */
export const railRange = (visible: number[], total: number) => (visible.length ? `${visible[0]}–${visible[visible.length - 1]} of ${total}` : `0 of ${total}`)

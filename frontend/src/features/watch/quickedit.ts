/** "Edit video" (quick edit) rules: title, several performers, a studio, and provider video-data matches. Ported from the classic dialog. */

export interface Person { name: string; id?: number | null; provider?: string | null; provider_id?: string | number | null; image?: string | null; profile?: string | null; aliases?: unknown[]; kind?: 'local' | 'provider' | 'create' }
export interface Studio { name: string; id?: number | null; provider?: string | null; provider_id?: string | number | null; image?: string | null; kind?: string }
export interface Item extends Person { count?: number; create?: boolean; exact?: boolean; alias_exact?: boolean; localMatch?: boolean; selected?: boolean; provider_ids?: unknown }

/** Names compare without accents, case, spaces or punctuation: "Zoë  O'Neil" equals "zoe oneil". */
export const nameKey = (v: { name?: string } | string | null | undefined) =>
  String(typeof v === 'string' ? v : (v?.name ?? '')).normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '')

export const samePerformer = (a: Person, b: Person) => {
  const sameName = nameKey(a) === nameKey(b)
  const sameProviderId = Boolean(a.provider_id && b.provider_id && String(a.provider ?? '').toLowerCase() === String(b.provider ?? '').toLowerCase() && String(a.provider_id) === String(b.provider_id))
  return sameName || sameProviderId
}

/** The video's performers, each once (by name key). Accepts names or objects, or a comma-separated string. */
export function initialPeople(raw: unknown): Person[] {
  const list: unknown[] = Array.isArray(raw) ? raw : String(raw ?? '').split(',').map((x) => x.trim()).filter(Boolean)
  const seen = new Set<string>()
  const out: Person[] = []
  for (const p of list) {
    const item: Person = typeof p === 'string' ? { name: p, id: null } : { ...(p as Person), name: String((p as Person)?.name ?? '').trim() }
    const key = nameKey(item)
    if (item.name && key && !seen.has(key)) { seen.add(key); out.push(item) }
  }
  return out
}
export const initialStudio = (v: { site_name?: string | null; studio?: string | null; site_id?: number | null }): Studio | null => {
  const name = String(v.site_name || v.studio || '')
  return name ? { name, id: v.site_id ?? null } : null
}

// ── search results ──
export interface LocalHit { id: number; name: string; video_count?: number; image?: string | null; provider_ids?: unknown }
export interface ProviderHit { name?: string; provider?: string; provider_id?: string | number; image?: string | null; profile?: string | null; aliases?: unknown[]; exact?: boolean; alias_exact?: boolean }

const providerKey = (x: { provider?: string | null; provider_id?: string | number | null }) => `${String(x.provider ?? '').toLowerCase()}:${String(x.provider_id)}`

/**
 * Rows for a performer or studio search: local matches first, then provider candidates (shown even when they share a name,
 * so the provider identity can be chosen on purpose), then "create new" unless it already exists or is already staged.
 */
export function searchItems(q: string, local: readonly LocalHit[], provider: readonly ProviderHit[], staged: { performers: readonly Person[]; studio: Studio | null }): Item[] {
  const items: Item[] = local.map((x) => ({ name: x.name, id: x.id, count: x.video_count ?? 0, image: x.image ?? null, provider_ids: x.provider_ids ?? null, kind: 'local' }))
  if (q.length >= 2) {
    for (const x of provider) {
      const key = String(x.name ?? '').toLowerCase().replace(/[^a-z0-9]/g, '')
      if (!key || !x.name || !x.provider_id) continue
      if (items.some((i) => i.kind === 'provider' && providerKey(i) === providerKey(x))) continue
      const sameLocal = items.find((i) => String(i.name ?? '').toLowerCase().replace(/[^a-z0-9]/g, '') === key)
      items.push({ name: x.name, id: null, count: 0, image: x.image ?? null, kind: 'provider', provider: x.provider, provider_id: x.provider_id, profile: x.profile ?? null, aliases: x.aliases ?? [], exact: Boolean(x.exact), alias_exact: Boolean(x.alias_exact), localMatch: Boolean(sameLocal) })
    }
    const nq = nameKey(q)
    const exactLocal = items.some((i) => i.kind === 'local' && nameKey(i) === nq)
    const staged_ = staged.performers.some((p) => nameKey(p) === nq) || nameKey(staged.studio) === nq
    if (!exactLocal && !staged_) items.push({ name: q, id: null, count: 0, image: null, kind: 'create', create: true })
  }
  const create = items.find((i) => i.create)
  const visible = items.filter((i) => !i.create).slice(0, create ? 23 : 24)
  if (create) visible.push(create)
  return visible
}
/** With nothing typed the list shows what is already chosen. */
export const stagedItems = (people: readonly Person[], studio: Studio | null, kind: 'performer' | 'studio'): Item[] =>
  kind === 'performer' ? people.map((p) => ({ ...p, count: 0, image: p.image ?? null, kind: p.kind ?? 'local', selected: true })) : studio ? [{ name: studio.name, id: studio.id ?? null, count: 0, image: studio.image ?? null, kind: 'local' }] : []

export const originLabel = (x: Item, kind: 'performer' | 'studio') =>
  x.create ? `Create a new local ${kind}` : x.kind === 'provider' ? `${String(x.provider || 'provider').toUpperCase()} · ${x.exact ? 'Exact name' : x.alias_exact ? 'Alias match' : 'Provider candidate'}${x.localMatch ? ' · matches local record' : ''}` : 'Local catalogue'

// ── choosing ──
/** Picking toggles a performer; picking a provider result for someone already chosen (without a provider id) enriches them instead of removing. */
export function pickPerformer(current: readonly Person[], x: Item): Person[] {
  const next = [...current]
  if (x.create) {
    if (next.findIndex((p) => nameKey(p) === nameKey(x.name)) < 0) next.push({ name: x.name.trim(), id: null, provider: null, provider_id: null, kind: 'local' })
    return next
  }
  const at = next.findIndex((p) => samePerformer(p, x))
  if (at >= 0) {
    const old = next[at]!
    if (x.kind === 'provider' && old.kind !== 'provider' && !old.provider_id) {
      next[at] = { ...old, ...x, id: old.id || x.id || null, name: String(x.name || old.name).trim(), provider: x.provider, provider_id: x.provider_id ?? null, image: x.image || old.image || null, profile: x.profile || old.profile || null }
    } else next.splice(at, 1)
  } else next.push({ ...x, name: String(x.name || '').trim(), provider_id: x.provider_id ?? null, provider: x.provider ?? null })
  return next
}
export const pickStudio = (x: Item): Studio => (x.create ? { name: x.name.trim(), id: null, provider: null, provider_id: null, kind: 'local' } : { name: x.name, id: x.id ?? null, provider: x.provider, provider_id: x.provider_id, image: x.image, kind: x.kind })

// ── provider video-data matches ──
interface PerformerDetail { name?: string; id?: string | number | null; provider?: string | null; provider_id?: string | number | null; image?: string | null; image_url?: string | null; profile?: string | null; url?: string | null; aliases?: unknown[] }
export interface Match { title?: string; date?: string; release_date?: string; production_date?: string; performers?: Array<string | PerformerDetail>; performer_details?: Array<string | PerformerDetail>; studio?: string; site?: string; studio_id?: number | string | null; studio_image?: string; provider?: string; source?: string; thumbnail?: string; image?: string; poster?: string; score_percent?: number | string; confidence_percent?: number | string; confidence?: number | string; _archive_confidence?: number | string }
export interface CandidateView { title: string; detail: string; cast: string; thumb: string; confidence: string; tone: 'good' | 'warn' | 'bad' | 'neutral' }
export const safeHttpUrl = (u: unknown) => { try { const x = new URL(String(u ?? '')); return x.protocol === 'http:' || x.protocol === 'https:' ? x.href : '' } catch { return '' } }

export function confidenceOf(raw: unknown): { text: string; tone: CandidateView['tone'] } {
  let text = String(raw ?? 'Review'), n: number | null = null
  if (typeof raw === 'number') n = raw <= 1 ? raw * 100 : raw
  else { const m = text.match(/(\d+(?:\.\d+)?)\s*%?/); if (m) n = Number(m[1]) <= 1 ? Number(m[1]) * 100 : Number(m[1]) }
  if (n !== null && Number.isFinite(n)) { n = Math.max(0, Math.min(100, n)); text = `${Math.round(n)}%` }
  return { text, tone: n === null ? 'neutral' : n >= 90 ? 'good' : n >= 70 ? 'warn' : 'bad' }
}
export function candidateView(m: Match): CandidateView {
  const release = m.date || m.release_date || m.production_date || 'Release date unknown'
  const cast = (m.performers || m.performer_details || []).slice(0, 4).map((p) => (typeof p === 'string' ? p : String(p?.name ?? ''))).filter(Boolean).join(', ') || 'No performer data'
  const source = String(m.provider || m.source || 'provider').toUpperCase()
  const c = confidenceOf(m.score_percent ?? m.confidence_percent ?? m.confidence ?? m._archive_confidence ?? 'Review')
  return { title: m.title || 'Untitled', detail: [source, m.studio || m.site || 'No studio data', release].filter(Boolean).join(' · '), cast, thumb: safeHttpUrl(m.thumbnail || m.image || m.poster || ''), confidence: c.text, tone: c.tone }
}

/** Applying a match stages (does not save) its title, whole cast and studio. */
export function applyMatch(m: Match): { title: string | null; performers: Person[]; studio: Studio | null; message: string } {
  const raw = (Array.isArray(m.performer_details) && m.performer_details.length ? m.performer_details : Array.isArray(m.performers) ? m.performers : []) as Array<string | Record<string, unknown>>
  const cast: Person[] = []
  const seen = new Set<string>()
  for (const r of raw) {
    const p = (typeof r === 'string' ? { name: r } : { ...r }) as Record<string, unknown>
    const name = String(p.name ?? '').trim(), key = nameKey(name)
    if (!name || !key || seen.has(key)) continue
    seen.add(key)
    cast.push({ name, id: null, provider: String(p.provider || m.provider || m.source || ''), provider_id: (p.provider_id ?? p.id ?? null) as string | number | null, image: (p.image || p.image_url || null) as string | null, profile: (p.profile || p.url || null) as string | null, aliases: (p.aliases as unknown[]) || [] })
  }
  const studioName = m.studio || m.site
  return {
    title: m.title ? String(m.title) : null,
    performers: cast,
    studio: studioName ? { name: String(studioName), id: (m.studio_id as number | null) ?? null, provider: m.provider || m.source, provider_id: m.studio_id ?? null, image: m.studio_image || m.image } : null,
    message: `Provider cast staged (${cast.length} performer${cast.length === 1 ? '' : 's'}). No database change has been made yet.`,
  }
}

// ── lookup ──
export const LOOKUP_NEEDS_INPUT = 'Enter a title, performer, or studio to find video data.'
export const LOOKUP_FAILED = 'Video-data lookup failed. Check the network and provider configuration, then try again.'
export const LOOKUP_IDLE = 'Filename clues and provider candidates will appear here. Use Find video data to search.'
export const LOOKUP_SEARCHING = 'Finding video data from the filename and configured providers…'
export const lookupAllowed = (title: string, performer: string, studio: string) => title.length >= 2 || performer.length >= 2 || studio.length >= 2

/** A typed query takes priority over the staged entity; with nothing typed, the staged one is a useful clue. */
export function lookupBody(i: { cid: string; mid: number; title: string; performerQuery: string; studioQuery: string; performers: readonly Person[]; studio: Studio | null; performerPicked: boolean; studioPicked: boolean }) {
  const performer = i.performerQuery || String(i.performers[0]?.name ?? '').trim()
  const studio = i.studioQuery || String(i.studio?.name ?? '').trim()
  return {
    channel_id: i.cid, message_id: i.mid, title: i.title, performer, performers: i.performers.map((p) => p.name), studio,
    performer_query: i.performerQuery, studio_query: i.studioQuery, performer_selected: i.performers[0]?.name ?? '', studio_selected: i.studio?.name ?? '',
    performer_selected_explicit: i.performerPicked, studio_selected_explicit: i.studioPicked,
  }
}
export interface LookupResult { ok?: boolean; detail?: string; matches?: Match[]; method?: string; errors?: unknown[]; filtered_count?: number; mapping_filter_names?: { performer?: string[]; studio?: string[] }; filename_hints?: { performers?: string[]; performer_candidates?: string[]; studio?: string; studio_candidates?: string[]; date?: string; year?: string | number } }
export function lookupStatus(r: LookupResult): string {
  const matches = r.matches ?? []
  const f = r.mapping_filter_names ?? {}
  const parts = [...(f.performer?.length ? [`performer: ${f.performer.join(', ')}`] : []), ...(f.studio?.length ? [`studio: ${f.studio.join(', ')}`] : [])]
  const note = parts.length ? ` Filtered to confirmed ${parts.join(' and ')}.` : ''
  if (matches.length) return `${matches.length} video-data candidate${matches.length === 1 ? '' : 's'} found via ${String(r.method || 'provider search')}.${note} Apply a match to stage it; Save changes commits it and syncs the Telegram caption.`
  const hidden = Number(r.filtered_count)
  if (hidden > 0) return `No candidates match the confirmed performer/studio mapping. ${hidden} conflicting result${hidden === 1 ? ' was' : 's were'} hidden. Enter or select a different entity only if you intentionally want to change the mapping.`
  const errors = (r.errors ?? []).filter(Boolean)
  return errors.length ? `No matching video data. Provider issue: ${errors.slice(0, 2).join(' · ')}` : 'No matching video data. The filename clues were checked against configured providers; try a shorter title or a more specific performer/studio.'
}
export interface Clue { label: string; kind: 'entity' | 'unverified' | 'date' }
export function hintChips(h: LookupResult['filename_hints']): Clue[] {
  const hints = h ?? {}
  const out: Clue[] = []
  for (const n of hints.performers ?? []) if (n) out.push({ label: String(n), kind: 'entity' })
  if (!out.length) for (const n of hints.performer_candidates ?? []) if (n) out.push({ label: String(n), kind: 'unverified' })
  if (hints.studio) out.push({ label: String(hints.studio), kind: 'entity' })
  else for (const n of hints.studio_candidates ?? []) if (n) out.push({ label: String(n), kind: 'unverified' })
  if (hints.date || hints.year) out.push({ label: String(hints.date || hints.year), kind: 'date' })
  return out
}

// ── save ──
/** Only the fields the server reads. UI state such as `selected`, `count` or `kind` must not leak into the request. */
const personForSave = (p: Person) => ({ name: String(p.name || '').trim(), id: p.id ?? null, provider: p.provider ?? null, provider_id: p.provider_id ?? null, image: p.image ?? null, profile: p.profile ?? null })
const studioForSave = (s: Studio) => ({ name: String(s.name || '').trim(), id: s.id ?? null, provider: s.provider ?? null, provider_id: s.provider_id ?? null, image: s.image ?? null })
export const saveBody = (cid: string, mid: number, title: string, performers: readonly Person[], studio: Studio | null) =>
  ({ channel_id: cid, message_id: mid, title: title.trim(), performers: performers.map(personForSave).filter((p) => p.name), studio: studio ? studioForSave(studio) : null, lock: true })

export interface CaptionSync { ok?: boolean; status?: string; detail?: string }
export interface SaveOutcome { close: boolean; status: string; toast: string }
/** A save always stores the metadata locally; the Telegram caption sync can fail separately, in which case the dialog stays open to retry. */
export function saveOutcome(sync: CaptionSync | undefined): SaveOutcome {
  if (sync && !sync.ok) {
    return {
      close: false,
      status: `Local metadata was saved, but Telegram caption sync failed. ${sync.detail || 'Check the Telegram connection and edit permissions, then press Save changes again to retry.'}`,
      toast: sync.status === 'partial' ? 'Some linked Telegram captions updated; some failed.' : sync.status === 'permission_denied' ? 'Metadata saved locally; Telegram edit permissions are missing.' : 'Metadata saved locally; Telegram caption sync failed. Press Save changes to retry.',
    }
  }
  return { close: true, status: '', toast: sync?.status === 'synced' ? 'Metadata saved and Telegram caption synchronized.' : sync?.status === 'unchanged' ? 'Metadata saved; Telegram caption is already up to date.' : 'Metadata saved and caption-sync request completed.' }
}

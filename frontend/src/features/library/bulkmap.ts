/** Bulk performer / studio mapping rules, ported from the classic pickers (pk, sk and the combined psmap dialog). */
import type { Picked } from './selection'
import { studioChoices, type StudioChoice, type StudioHit } from './mapping'

export type MapMode = 'performer' | 'site' | 'both' | 'all'

export interface PerformerHit { id: number; name: string; video_count?: number; image?: string | null }
export interface ProviderHit { name?: string; provider?: string; provider_id?: string; image?: string | null; profile?: string | null; exact?: boolean; alias_exact?: boolean }
export interface PerformerChoice {
  name: string; id: number | null; count: number; image: string | null; kind: 'local' | 'provider'
  provider?: string; provider_id?: string; profile?: string | null; exact?: boolean; alias_exact?: boolean
}
export const providerLabel = (p?: string) => (p === 'stashdb' ? 'StashDB' : 'ThePornDB')

/** Local performers first, then provider ("API") matches that carry an id, de-duplicated; at most 28 rows. */
export function performerChoices(local: readonly PerformerHit[], provider: readonly ProviderHit[]): PerformerChoice[] {
  const localItems: PerformerChoice[] = local.map((p) => ({ name: p.name, id: p.id, count: p.video_count ?? 0, image: p.image ?? null, kind: 'local' }))
  const seen = new Set<string>()
  const apiItems: PerformerChoice[] = []
  for (const p of provider) {
    if (!p.name || !p.provider_id) continue
    const key = `${p.provider}:${p.provider_id}`
    if (seen.has(key)) continue
    seen.add(key)
    apiItems.push({ name: p.name, id: null, count: 0, image: p.image ?? null, kind: 'provider', provider: p.provider, provider_id: p.provider_id, profile: p.profile ?? null, exact: p.exact, alias_exact: p.alias_exact })
  }
  return [...localItems, ...apiItems].slice(0, 28)
}
export const performerNote = (c: PerformerChoice) =>
  c.kind === 'provider'
    ? `API match · ${providerLabel(c.provider)}${c.exact ? ' · exact name' : c.alias_exact ? ' · alias match' : ''}`
    : `Local performer · ${c.count} video${c.count === 1 ? '' : 's'} · link to this performer`

/** Provider search only starts at two characters. */
export const wantsProviderSearch = (q: string) => q.trim().length >= 2

type Body = Record<string, unknown>
const performerFields = (p: PerformerChoice): Body => ({ performer_id: p.id, performer_name: p.name.trim(), provider: p.provider ?? null, provider_id: p.provider_id ?? null, image: p.image ?? null, profile: p.profile ?? null })
const studioFields = (s: StudioChoice): Body => ({ site_id: s.id, studio_name: s.name.trim() })

export const performerBody = (picked: Picked[], p: PerformerChoice): Body => ({ videos: picked, field: 'performer', performer_id: p.id, target_name: p.name.trim(), provider: p.provider ?? null, provider_id: p.provider_id ?? null, image: p.image ?? null, profile: p.profile ?? null, action: 'move', lock: true })
export const studioBody = (picked: Picked[], s: StudioChoice): Body => ({ videos: picked, field: 'site', site_id: s.id, target_name: s.name.trim(), action: 'move', lock: true })

export interface Combo { title: string; performer: PerformerChoice | null; studio: StudioChoice | null }
/** "Map performer + studio" needs both. "Apply all metadata" needs at least one of title, performer, studio. */
export const canSave = (mode: MapMode, c: Combo) => (mode === 'all' ? Boolean(c.title.trim() || c.performer || c.studio) : Boolean(c.performer && c.studio))
export const saveProblem = (mode: MapMode) => (mode === 'all' ? 'Enter a title, or choose a performer or a studio.' : 'Select both a performer and a studio.')

export interface Step { kind: 'title' | 'mapping'; body: Body; failure: string }
/** Title first (if any), then the performer/studio part: both together, or just the one that was chosen. */
export function comboSteps(picked: Picked[], c: Combo): Step[] {
  const steps: Step[] = []
  const title = c.title.trim()
  if (title) steps.push({ kind: 'title', body: { videos: picked, field: 'title', target_name: title, action: 'move', lock: true }, failure: 'Title update failed.' })
  const { performer: p, studio: s } = c
  if (p || s) {
    const field = p && s ? 'performer_site' : p ? 'performer' : 'site'
    steps.push({ kind: 'mapping', failure: 'Combined mapping failed.', body: { videos: picked, field, action: 'move', lock: true, target_name: (p ?? s)!.name.trim(), ...(p ? performerFields(p) : {}), ...(s ? studioFields(s) : {}) } })
  }
  return steps
}

export const n = (changed: number | undefined, fallback: number) => Number(changed ?? fallback) || fallback
const vids = (k: number) => `${k} video${k === 1 ? '' : 's'}`
export const performerLinked = (name: string, changed: number) => `${name} linked to ${changed} selected video${changed === 1 ? '' : 's'}.`
export const studioLinked = (name: string, changed: number) => `${name} linked to ${vids(changed)}.`
export const comboDone = (changed: number, hadTitle: boolean, field: 'performer_site' | 'performer' | 'site') => `${vids(changed)} ${hadTitle ? 'updated' : `mapped to ${field === 'performer_site' ? 'performer + studio' : field === 'performer' ? 'performer' : 'studio'}`}.`
export const titleOnlyDone = (changed: number) => `Title applied to ${vids(changed)}.`

export const heading = (mode: MapMode) => ({ performer: 'Select performer', site: 'Select studio', both: 'Map performer + studio', all: 'Apply metadata' })[mode]
export const hint = (mode: MapMode) => (mode === 'all' ? 'Change any of: title, performer, studio. Empty parts are left as they are.' : 'Choose both values, then save once.')
export const saveLabel = (mode: MapMode) => (mode === 'all' ? 'Apply to selected videos' : 'Save performer + studio')
export const studioHits = (hits: unknown): StudioHit[] => (Array.isArray(hits) ? (hits as StudioHit[]) : [])
export { studioChoices }

/** Performer / studio editor rules (profile picture, name, profile URL, biography). Ported from the classic entity editor. */
export type Kind = 'performers' | 'studios'
export const isPerformer = (k: Kind) => k === 'performers'

export const titleFor = (k: Kind) => (isPerformer(k) ? 'Profile picture' : 'Studio image')
export const saveLabel = (k: Kind) => (isPerformer(k) ? 'Save profile picture' : 'Save studio image')
export const editUrl = (k: Kind, id: number) => `/api/${k}/${id}/edit`
export const candidatesUrl = (k: Kind, id: number, offset: number, limit = PAGE) => `/api/${k}/${id}/image-candidates?limit=${limit}&offset=${offset}`
export const imageUrl = (k: Kind, id: number, v: string | number | null | undefined) => `/api/${k}/${id}/image?v=${encodeURIComponent(String(v ?? ''))}`
export const PAGE = 10

export interface Candidate { url: string; provider?: string; width?: number; height?: number; current?: boolean }
const LABELS: ReadonlyArray<[string, string]> = [['theporndb', 'ThePornDB'], ['stashdb', 'STASHDB']]
export interface Group { key: string; label: string; items: Array<{ index: number; c: Candidate; caption: string }> }
/** Provider images grouped ThePornDB first, then StashDB; each tile says where it is from and how big it is. */
export function groups(cands: readonly Candidate[]): Group[] {
  return LABELS.map(([key, label]) => ({
    key, label,
    items: cands.map((c, index) => ({ c, index })).filter((x) => String(x.c.provider ?? '').toLowerCase() === key).map(({ c, index }) => ({ index, c, caption: `${label}${c.width && c.height ? ` · ${c.width}×${c.height}` : ''}` })),
  })).filter((g) => g.items.length)
}
export function providerSummary(cands: readonly Candidate[], hasMore: boolean): string {
  if (!cands.length) return 'No provider images found.'
  const counts = cands.reduce<Record<string, number>>((m, c) => { const p = String(c.provider || 'provider').toUpperCase(); m[p] = (m[p] || 0) + 1; return m }, {})
  return `${Object.entries(counts).map(([p, n]) => `${p}: ${n}`).join(' · ')}${hasMore ? ' · More images available' : ''}`
}
export const footerFor = (n: number) => (n ? 'Select an image, then save.' : 'You can use a manual image URL or file.')
export const LOADING = 'Loading provider images…'
export const lookupFailed = (m: string) => `Provider image lookup failed: ${m}`
export const providerLabel = (p: string | undefined) => String(p || 'provider').toUpperCase()

export const MAX_BYTES = 8 * 1024 * 1024
export const fileProblem = (size: number) => (size > MAX_BYTES ? 'Image must be 8 MiB or smaller.' : '')
export const initialState = (locked: boolean) => (locked ? 'Locked current choice' : 'Current provider image')
export const STATE_LOCAL = 'Local image selected — save to apply'
export const STATE_RESET = 'Provider image will be restored on save'
export const stateProvider = (p: string | undefined) => `Selected ${providerLabel(p)} image — save to apply`

export interface Form { kind: Kind; name: string; manualUrl: string; reset: boolean; profile: string; bio: string; gender?: string; imageData: string; provider: Candidate | null }
/** A typed URL wins over a chosen provider image and over "restore"; an uploaded file wins over a provider image. */
export function saveBody(f: Form): Record<string, unknown> {
  const url = f.manualUrl.trim()
  const reset = url ? false : f.reset
  const provider = url ? null : f.provider
  const body: Record<string, unknown> = { name: f.name.trim(), image_url: url, reset_image_to_api: reset }
  if (isPerformer(f.kind)) { body.profile = f.profile.trim(); body.bio = f.bio.trim(); if (f.gender !== undefined) body.gender = f.gender }
  if (f.imageData) body.image_data = f.imageData
  if (!f.imageData && !url && !reset && provider?.url) { body.provider_image_url = provider.url; body.provider_image_provider = provider.provider || 'provider' }
  return body
}
export const nameProblem = (n: string) => (n.trim() ? '' : 'Enter a name.')
export function savedMessage(f: Form): string {
  const url = f.manualUrl.trim()
  if (f.provider && !f.imageData && !url) return 'Selected profile image saved.'
  return f.reset && !url ? 'Provider profile image restored.' : 'Profile picture saved.'
}
export const saveFailed = (d: string) => `Save failed: ${d || 'error'}`
export const loadFailed = (d: string) => `Could not load entity: ${d || 'error'}`

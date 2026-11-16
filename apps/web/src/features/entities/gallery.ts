/** Pure logic for the performer Gallery tab: the pictures the providers hold of a performer, kept on this PC and used to recognise them. */
export interface GalleryItem { id: number; provider: string; width: number; height: number; faces: number; reference: boolean; source: string; url: string }
export interface GalleryState { ok?: boolean; items: GalleryItem[]; removed: number; syncing: boolean; last_synced_at: string; last_result: string; references: number }

export const GALLERY_EMPTY = 'No gallery pictures yet. Get them from the providers: they are kept on this PC and help recognise this performer in your photos.'
export const GALLERY_PROVIDER_FAILED = 'The providers could not be reached for this performer. Try again later.'

const PROVIDERS: Record<string, string> = { stashdb: 'StashDB', theporndb: 'ThePornDB' }
export const providerName = (p: string): string => PROVIDERS[p.trim().toLowerCase()] ?? (p.trim() || 'Provider')

/** "14 pictures · 11 used to recognise her · 2 removed" */
export function galleryStatus(s: Pick<GalleryState, 'items' | 'references' | 'removed'>): string {
  const n = s.items.length
  const parts = [`${n} picture${n === 1 ? '' : 's'}`]
  if (n) parts.push(`${s.references} used for face matching`)
  if (s.removed) parts.push(`${s.removed} removed`)
  return parts.join(' · ')
}

/** When the providers were last asked, in the reader's own words. */
export function lastSyncText(iso: string, result: string, now: Date = new Date()): string {
  if (!iso) return result && result !== 'ok' ? GALLERY_PROVIDER_FAILED : 'Not fetched from the providers yet.'
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return ''
  const days = Math.floor((now.getTime() - then.getTime()) / 86_400_000)
  return days <= 0 ? 'Updated today.' : days === 1 ? 'Updated yesterday.' : `Updated ${days} days ago.`
}

/** Why one picture does or does not help recognise the performer. */
export function referenceNote(i: Pick<GalleryItem, 'faces' | 'reference'>): string {
  if (i.reference) return 'Used for face matching'
  if (i.faces === 0) return 'No face found, so not used for matching'
  if (i.faces > 1) return 'Several faces, so not used for matching'
  return 'Not read yet'
}

export const galleryTabCount = (s: Pick<GalleryState, 'items'> | undefined): number => (Array.isArray(s?.items) ? s.items.length : 0)

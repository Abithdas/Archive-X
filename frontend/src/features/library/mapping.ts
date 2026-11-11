/** "Mapping / report" rules: correct a video's title and studio. Ported from the classic metadata editor. */

export interface StudioHit { id: number; name: string; video_count?: number; image?: string | null }
export interface StudioChoice { name: string; id: number | null; count: number; create: boolean; image?: string | null }
export interface PickedStudio { name: string; id: number | null }

export const NO_STUDIOS_HINT = 'Type a studio name to search.'

/** Search hits (the single-video dialog waits for text; bulk pickers list at once), plus a "create new" row unless the typed name already matches one exactly (case-insensitive). */
export function studioChoices(hits: readonly StudioHit[], query: string, opts: { listWhenEmpty?: boolean } = {}): StudioChoice[] {
  const q = query.trim()
  if (!q && !opts.listWhenEmpty) return []
  const out: StudioChoice[] = hits.map((h) => ({ name: h.name, id: h.id, count: h.video_count ?? 0, create: false, image: h.image ?? null }))
  if (!q) return out   // the bulk pickers list studios before anything is typed; "create" only makes sense once there is a name
  if (!hits.some((h) => String(h.name ?? '').toLowerCase() === q.toLowerCase())) out.push({ name: q, id: null, count: 0, create: true })
  return out
}
export const choiceLabel = (c: StudioChoice) => (c.create ? `Create new studio “${c.name}”` : c.name)
export const choiceNote = (c: StudioChoice) => (c.create ? '' : `${c.count} video${c.count === 1 ? '' : 's'} · link to this studio`)
export const pickedFrom = (c: StudioChoice): PickedStudio => ({ name: c.name.trim(), id: c.id })

export interface MappingTask { field: 'title' | 'site'; target_name: string; action: 'move'; lock: true; site_id?: number | null }
/** Only what changed is sent. An empty title is ignored; clearing the studio is an explicit change. Both are locked so provider syncs do not undo them. */
export function mappingTasks(input: { title: string; originalTitle: string; studio: PickedStudio | null; originalStudio: string }): MappingTask[] {
  const tasks: MappingTask[] = []
  const title = input.title.trim()
  if (title && title !== input.originalTitle.trim()) tasks.push({ field: 'title', target_name: title, action: 'move', lock: true })
  const picked = input.studio ? input.studio.name.trim() : ''
  if (picked !== input.originalStudio.trim()) tasks.push({ field: 'site', site_id: input.studio?.id ?? null, target_name: picked, action: 'move', lock: true })
  return tasks
}
export const mappingBody = (item: { _cid: string; id: number }, task: MappingTask) => ({ videos: [{ channel_id: item._cid, message_id: item.id }], ...task })

export const NO_CHANGES = 'No changes to save.'
export const SAVED = 'Video title/studio mapping updated.'
/** If a later request fails, the earlier ones are already stored: say so. */
export const failureMessage = (detail: string, appliedFields: readonly string[]) =>
  `${detail || 'Could not save mapping.'}${appliedFields.length ? ` (Already saved: ${appliedFields.join(', ')}.)` : ''}`

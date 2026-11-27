/** Suggestions made from a video's own snapshots (a studio's watermark, performers' faces). They are only ever proposals: nothing changes until Save. */
import { nameKey, type Person, type Studio } from './quickedit'

export interface SnapshotSuggestion { kind: 'performer' | 'studio'; id: number; name: string; score: number; evidence: { frames?: number; corner?: string } }
export interface SnapshotRun { suggestions: SnapshotSuggestion[]; frames?: number; faces_checked?: boolean; studio_has?: boolean; people_has?: boolean; faces_known?: number; marks_known?: number }

const CORNERS: Record<string, string> = { tl: 'top-left', tr: 'top-right', bl: 'bottom-left', br: 'bottom-right' }

/** The server's list, keeping only well-formed rows. */
export function suggestionList(raw: unknown): SnapshotSuggestion[] {
  const rows = (raw as { suggestions?: unknown } | null)?.suggestions
  if (!Array.isArray(rows)) return []
  const out: SnapshotSuggestion[] = []
  for (const r of rows as Array<Partial<SnapshotSuggestion>>) {
    if ((r?.kind !== 'performer' && r?.kind !== 'studio') || !Number.isFinite(Number(r.id)) || !String(r.name ?? '').trim()) continue
    out.push({ kind: r.kind, id: Number(r.id), name: String(r.name).trim(), score: Number(r.score) || 0, evidence: (r.evidence as SnapshotSuggestion['evidence']) ?? {} })
  }
  return out
}

/** Why it was suggested, in words. */
export function reasonOf(s: SnapshotSuggestion): string {
  if (s.kind === 'studio') return `Watermark in the ${CORNERS[String(s.evidence.corner)] ? CORNERS[String(s.evidence.corner)] + ' corner' : 'corner'} matches this studio`
  const n = Number(s.evidence.frames) || 0
  return n > 0 ? `Face matched in ${n} snapshot${n === 1 ? '' : 's'}` : 'Face matched in the snapshots'
}

/** Suggestions still worth showing: not already chosen in the dialog. */
export function openSuggestions(list: readonly SnapshotSuggestion[], performers: readonly Person[], studio: Studio | null): SnapshotSuggestion[] {
  return list.filter((s) => (s.kind === 'performer' ? !performers.some((p) => p.id === s.id || nameKey(p) === nameKey(s.name)) : !(studio && (studio.id === s.id || nameKey(studio) === nameKey(s.name)))))
}

/** What the "Check snapshots" button reports: what was found, or why nothing was (so there is something to do about it). */
export function runNote(r: SnapshotRun): string {
  if (!r.frames) return 'This video has no snapshots yet, so there is nothing to look at. Make its snapshots from Transfers first (Metadata & Assets, stage Snapshots).'
  if (r.frames < 12) return `Only ${r.frames} snapshots exist for this video; at least 12 are needed to tell a studio mark from the picture.`
  const found = suggestionList(r).length
  if (found) return `Found ${found} suggestion${found === 1 ? '' : 's'} from ${r.frames} snapshots.${r.faces_checked === false ? ' Faces were not checked: the face models are not on this PC yet (open People once to install them).' : ''}`
  const why: string[] = []
  if (r.faces_checked === false) why.push('faces were not checked: the face models are not on this PC yet (open People once to install them)')
  else if (r.people_has) why.push('the video already has performers, so faces were not read')
  else if (r.faces_known === 0) why.push('no performer has a reference face yet (name some faces in Photos, People, or give performers a picture, then check again)')
  else why.push(`no face matched any of the ${r.faces_known} known faces`)
  if (r.studio_has) why.push('the video already has a studio')
  else if (r.marks_known === 0) why.push('no studio watermark is known yet (they are learned from videos that already have a studio)')
  else why.push(`no watermark matched any of the ${r.marks_known} known studios`)
  return `Looked at ${r.frames} snapshots and found nothing: ${why.join('; ')}.`
}

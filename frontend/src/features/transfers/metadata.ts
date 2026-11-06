/** Metadata & Assets run options → API payloads (ported from runUnifiedMetadataAssets / processPendingLibraryItems). */
export type Stage = 'inventory' | 'metadata' | 'performers' | 'channel_data' | 'snapshots'
export const ENTITY_STAGES: Stage[] = ['metadata', 'performers', 'channel_data']
export const STAGE_ORDER: Stage[] = ['inventory', 'metadata', 'performers', 'channel_data', 'snapshots']
const LABELS: Record<Stage, string> = { inventory: 'Inventory', metadata: 'Metadata & Entity Data', performers: 'Metadata & Entity Data', channel_data: 'Metadata & Entity Data', snapshots: 'Build snapshots' }

export interface RunOptions {
  inventory: boolean
  entities: boolean
  snapshots: boolean
  scope: 'all' | '25' | '50' | '100' | '250'
  forceMetadata: boolean
  forceEntity: boolean
  fullInventory: boolean
}
export const DEFAULT_OPTIONS: RunOptions = { inventory: true, entities: true, snapshots: true, scope: 'all', forceMetadata: false, forceEntity: false, fullInventory: false }

/** The three visible stage switches → concrete stage ids (entity data always runs as one pipeline). */
export function selectedStages(o: RunOptions, forPending = false): Stage[] {
  const out: Stage[] = []
  if (o.inventory && !forPending) out.push('inventory')
  if (o.entities) out.push(...ENTITY_STAGES)
  if (o.snapshots) out.push('snapshots')
  return out
}
export function expandStages(stages: Stage[]): Stage[] {
  const sel = new Set(stages)
  if (ENTITY_STAGES.some((x) => sel.has(x))) ENTITY_STAGES.forEach((x) => sel.add(x))
  return STAGE_ORDER.filter((x) => sel.has(x))
}
export function stageLabels(stages: Stage[]): string[] {
  const out: string[] = []
  for (const st of expandStages(stages)) if (!out.includes(LABELS[st])) out.push(LABELS[st])
  return out
}
export function scopeBody(o: RunOptions): { limit: number; unbounded: boolean } {
  return o.scope === 'all' ? { limit: 50, unbounded: true } : { limit: Math.max(25, Number(o.scope) || 50), unbounded: false }
}
/** Full run. Full inventory also reconciles delivered-post captions, as in the classic UI. */
export function processBody(channels: string[], stages: Stage[], o: RunOptions, autoAdded: Record<string, string[]> = {}) {
  return { channel_ids: channels, stages, ...scopeBody(o), force_metadata: o.forceMetadata, force_entity: o.forceEntity, full_inventory: o.fullInventory, reconcile_uploaded_captions: o.fullInventory, auto_added_prerequisites: autoAdded }
}
export const pendingBody = (channels: string[], o: RunOptions) => ({ channel_ids: channels, stages: selectedStages(o, true), ...scopeBody(o), force_metadata: o.forceMetadata, force_entity: o.forceEntity })

export interface Preflight { ok?: boolean; ready?: boolean; detail?: string; required_stages?: Stage[]; missing_by_channel?: Array<{ channel_id: string | number; missing?: Stage[] }> }
/** What the preflight asks to add before the run: stages to add, and which channels lacked which stage. */
export function prerequisitePlan(pf: Preflight, chosen: Stage[]) {
  const required = pf.required_stages ?? []
  const autoAdded: Record<string, string[]> = {}
  for (const item of pf.missing_by_channel ?? []) for (const st of item.missing ?? []) (autoAdded[st] ??= []).push(String(item.channel_id))
  return { required, labels: stageLabels(required), affected: (pf.missing_by_channel ?? []).map((x) => String(x.channel_id)), stages: expandStages([...required, ...chosen]), autoAdded }
}

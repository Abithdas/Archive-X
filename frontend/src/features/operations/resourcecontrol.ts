/** Resource Controller: choose a policy, or set manual per-lane limits. Ported from the classic Operations page. */
type Obj = Record<string, unknown>
const o = (v: unknown): Obj => (v && typeof v === 'object' ? (v as Obj) : {})

export type Mode = 'automatic' | 'playback' | 'balanced' | 'background' | 'manual'
export const MODES: ReadonlyArray<{ value: Mode; label: string }> = [
  { value: 'automatic', label: 'Automatic' }, { value: 'playback', label: 'Playback Priority' }, { value: 'balanced', label: 'Balanced' }, { value: 'background', label: 'Background Priority' }, { value: 'manual', label: 'Manual' },
]
export const LANE_KEYS = ['metadata', 'thumbnail', 'preview', 'conversion', 'upload'] as const
export type Lane = (typeof LANE_KEYS)[number]
export const LANE_LABELS: Record<Lane, string> = { metadata: 'Metadata', thumbnail: 'Thumbnail', preview: 'Preview', conversion: 'Conversion', upload: 'Upload' }
export const DEFAULT_LIMITS: Record<Lane, number> = { metadata: 3, thumbnail: 1, preview: 1, conversion: 2, upload: 2 }
export const EXPIRIES: ReadonlyArray<{ value: number; label: string }> = [{ value: 0, label: 'Until changed' }, { value: 900, label: '15 minutes' }, { value: 1800, label: '30 minutes' }, { value: 3600, label: '1 hour' }]

export const control = (res: Obj) => o(res.control)
export const modeOf = (res: Obj): Mode => { const m = String(control(res).mode ?? 'automatic').toLowerCase(); return MODES.some((x) => x.value === m) ? (m as Mode) : 'automatic' }
export const modeLabel = (res: Obj) => String(control(res).mode_label ?? '') || MODES.find((x) => x.value === modeOf(res))!.label
export const isPaused = (res: Obj) => Boolean(res.background_paused || control(res).background_paused)
export const playbackGate = (res: Obj) => Boolean(control(res).playback_gate)

/** Limits are whole numbers from 1 to 8; anything unreadable becomes 1. */
export const clampLimit = (v: unknown) => Math.max(1, Math.min(8, Math.round(Number(v || 1)) || 1))
/** The saved manual limits, with the classic defaults for any lane the server has not set. */
export function limitsFrom(res: Obj): Record<Lane, number> {
  const saved = o(control(res).manual_limits)
  return Object.fromEntries(LANE_KEYS.map((k) => [k, saved[k] != null ? clampLimit(saved[k]) : DEFAULT_LIMITS[k]])) as Record<Lane, number>
}

const LABELS: Record<string, string> = { automatic: 'Operator selected Automatic', playback: 'Operator selected Playback Priority', balanced: 'Operator selected Balanced', background: 'Operator selected Background Priority' }
export const modeBody = (mode: Exclude<Mode, 'manual'>, expires = 0) => ({ mode, expires_seconds: expires, label: LABELS[mode] ?? 'Operator selected resource policy' })
export const manualBody = (limits: Record<Lane, unknown>, gate: boolean, expires: number) => ({
  mode: 'manual', limits: Object.fromEntries(LANE_KEYS.map((k) => [k, clampLimit(limits[k])])), playback_gate: gate, expires_seconds: expires, label: 'Manual resource limits',
})
export const pauseBody = (paused: boolean) => ({ background_paused: !paused, label: !paused ? 'Background controller paused by operator' : 'Background controller resumed' })

export const DRAFT_HINT = 'Manual mode is not active yet. Review the limits, then choose “Save & use Manual mode”.'
export const pauseLabel = (paused: boolean) => (paused ? 'Resume background controller' : 'Pause background controller')
export const MODE_FAILED = 'Could not change resource policy.'
export const MANUAL_FAILED = 'Could not apply resource limits. Manual edits have been kept.'
export const PAUSE_FAILED = 'Could not change background controller.'
export const modeChanged = (r: { resources?: { control?: { mode_label?: string } } }, mode: string) => `Resource policy: ${r.resources?.control?.mode_label || mode}`
export const manualSaved = (expires: number) => (expires ? `Manual limits saved and active for ${Math.round(expires / 60)} minutes.` : 'Manual limits saved and active until changed.')
export const pauseDone = (wasPaused: boolean) => (!wasPaused ? 'Background controller paused. Running work will stop at safe checkpoints.' : 'Background controller resumed.')

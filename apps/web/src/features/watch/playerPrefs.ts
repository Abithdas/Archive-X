/** What the player remembers between videos and restarts, on this PC: the volume, mute and the speed. */
export interface PlayerPrefs { volume: number; muted: boolean; rate: number }
export const DEFAULT_PREFS: PlayerPrefs = { volume: 1, muted: false, rate: 1 }
const KEY = 'archive-x:player-prefs'
const RATES = [0.5, 0.75, 1, 1.25, 1.5, 2]

export function normalizePrefs(v: Partial<PlayerPrefs> | null | undefined): PlayerPrefs {
  const volume = Number(v?.volume)
  const rate = Number(v?.rate)
  return {
    volume: v?.volume !== undefined && Number.isFinite(volume) ? Math.min(1, Math.max(0, volume)) : DEFAULT_PREFS.volume,
    muted: v?.muted === true,
    rate: RATES.includes(rate) ? rate : DEFAULT_PREFS.rate,
  }
}
export function loadPrefs(): PlayerPrefs { try { return normalizePrefs(JSON.parse(localStorage.getItem(KEY) ?? 'null')) } catch { return DEFAULT_PREFS } }
export function savePrefs(patch: Partial<PlayerPrefs>): void {
  try { localStorage.setItem(KEY, JSON.stringify(normalizePrefs({ ...loadPrefs(), ...patch }))) } catch { /* private mode */ }
}

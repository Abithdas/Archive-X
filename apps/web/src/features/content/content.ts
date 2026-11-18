export type Mode = 'sfw' | 'nsfw'
export interface ContentState { ok?: boolean; mode: Mode; pin_set: boolean; locked_for: number }
export interface ChannelMark { telegram_id: string; title: string; username: string | null; in_library: number; content_rating: Mode; holds: 'videos' | 'photos' | 'both'; videos: number; photos: number }
export interface MarksPayload { ok?: boolean; mode: Mode; channels: ChannelMark[] }

export const MODES: ReadonlyArray<{ value: Mode; label: string; hint: string }> = [
  { value: 'sfw', label: 'SFW', hint: 'Movies and TV shows' },
  { value: 'nsfw', label: 'NSFW', hint: 'Adult videos and photos' },
]
export const modeLabel = (m: Mode) => (m === 'nsfw' ? 'NSFW' : 'SFW')
export const otherMode = (m: Mode): Mode => (m === 'nsfw' ? 'sfw' : 'nsfw')

/** Why a PIN cannot be used, or null: four to twelve digits. */
export const pinProblem = (pin: string): string | null => (/^\d{4,12}$/.test(pin) ? null : 'Use 4 to 12 digits.')

/** What the dialog must ask before the mode can change: nothing (towards SFW), the PIN, or a new PIN when none exists yet. */
export type Ask = 'none' | 'pin' | 'create'
export const askFor = (target: Mode, state: Pick<ContentState, 'pin_set'>): Ask => (target === 'sfw' ? 'none' : state.pin_set ? 'pin' : 'create')

export function switchBody(target: Mode, ask: Ask, pin: string) {
  return ask === 'pin' ? { mode: target, pin } : ask === 'create' ? { mode: target, new_pin: pin } : { mode: target }
}

/** A short line for the library when the mode shows nothing yet because no channel is marked for it. */
export function emptyHint(mode: Mode, channels: ReadonlyArray<Pick<ChannelMark, 'in_library' | 'content_rating'>>): string | null {
  if (channels.some((c) => c.in_library && c.content_rating === mode)) return null
  return `No channel is in the library as ${modeLabel(mode)} yet. Choose channels in Settings, Telegram, Channels.`
}

export const channelsOfMode = (channels: ChannelMark[], mode: Mode) => channels.filter((c) => c.in_library && c.content_rating === mode)

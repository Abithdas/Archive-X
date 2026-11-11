/** Shuffle a set of videos into one external VLC / MPC-HC playlist. Ported from the classic Library, performer and studio pages. */
export type Player = 'vlc' | 'mpc'
export interface Playable { _cid: string; id: number; name?: string }
export interface PlaylistItem { channel_id: string; message_id: number; name: string }

/** Fisher–Yates, returning a new array. `rand` is injectable so tests are deterministic. */
export function shuffled<T>(list: readonly T[], rand: () => number = Math.random): T[] {
  const a = [...list]
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1))
    ;[a[i], a[j]] = [a[j]!, a[i]!]
  }
  return a
}
export const playable = <T extends Playable>(list: readonly T[]): T[] => list.filter((v) => v && v.id != null && v._cid != null)
export const playlistItems = (list: readonly Playable[]): PlaylistItem[] => list.map((v) => ({ channel_id: v._cid, message_id: v.id, name: v.name || `video ${v.id}` }))
export const externalBody = (player: Player, items: PlaylistItem[]) => ({ player, random: true, playlist: items })

export const playerName = (p: Player | string) => (p === 'mpc' || p === 'mpc-hc' ? 'MPC-HC' : String(p).toUpperCase())

export const isRemoteHost = (hostname: string) => !['127.0.0.1', 'localhost', '::1', '[::1]'].includes(hostname)
export const isIOS = (ua: string, platform: string, touchPoints: number) => /iPad|iPhone|iPod/.test(ua) || (platform === 'MacIntel' && touchPoints > 1)
export const isAndroid = (ua: string) => /Android/i.test(ua)

/** How a shuffle reaches the player: launched on this PC, a playlist file downloaded, or VLC's mobile link. */
export type Route = 'launch' | 'download' | 'mobile-vlc'
export const routeFor = (player: Player, remote: boolean, mobile: boolean): Route => (!remote ? 'launch' : player === 'vlc' && mobile ? 'mobile-vlc' : 'download')

export const NO_LIBRARY_VIDEOS = 'Select a published channel first.'
export const NO_PERFORMER_VIDEOS = 'This performer has no videos in the local catalogue yet.'
export const NO_STUDIO_VIDEOS = 'This studio has no videos in the local catalogue yet.'
export const collecting = (n: number) => `Collecting videos for the shuffle… ${n.toLocaleString()} so far`
export const startingMessage = (player: Player, n: number) => `Starting random ${playerName(player)} playlist: ${n.toLocaleString()} video${n === 1 ? '' : 's'}…`
export const failedMessage = (player: Player, detail?: string) => `${playerName(player)} shuffle could not start: ${detail || 'unknown error'}`
export const startedMessage = (r: { player?: string; count?: number }, player: Player, n: number, remote: boolean) => `${r.player || playerName(player)} shuffled playlist started: ${r.count || n} video${(r.count || n) === 1 ? '' : 's'}${remote ? ' (on the PC)' : ''}`
export const downloadedMessage = (player: Player) => `Random playlist downloaded — open it in ${playerName(player)} on this device.`
export const NOT_ON_LAN = 'VLC device playback requires LAN access to Archive X.'

/** The mobile VLC hand-off URL for a generated playlist link, or '' when the platform has none. */
export function mobileVlcUrl(url: string, android: boolean, ios: boolean): string {
  if (android) return `intent://${url.replace(/^https?:\/\//, '')}#Intent;scheme=http;type=audio/x-mpegurl;package=org.videolan.vlc;end`
  if (ios) return `vlc-x-callback://x-callback-url/stream?url=${encodeURIComponent(url)}`
  return ''
}

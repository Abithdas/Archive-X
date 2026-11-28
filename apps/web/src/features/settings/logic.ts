/** Settings logic ported from the classic UI (loadSettingsStatus, validateSettingsV54, the Experience form). */
type Obj = Record<string, unknown>
const o = (v: unknown): Obj => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Obj) : {})
const s = (v: unknown) => String(v ?? '')

type Tone = 'ok' | 'warn' | 'bad' | ''
export interface StatusRow { icon: string; title: string; sub: string; tone: Tone; label: string; go?: PanelId }

export const PANELS = [
  { id: 'overview', title: 'Overview', blurb: 'Connection and system status', icon: 'grid' },
  { id: 'telegram', title: 'Telegram', blurb: 'API, session and sign-in', icon: 'send' },
  { id: 'local', title: 'Local files', blurb: 'Folders on this PC, SFW or NSFW', icon: 'folders' },
  { id: 'metadata', title: 'Metadata', blurb: 'ThePornDB, StashDB, scan window', icon: 'database' },
  { id: 'addons', title: 'Addons', blurb: 'Catalogue add-ons for Discover', icon: 'grid' },
  { id: 'experience', title: 'Experience', blurb: 'Autoplay, resume, seek preview', icon: 'play' },
  { id: 'appearance', title: 'Appearance', blurb: 'Zoom, themes, custom themes', icon: 'sparkle' },
  { id: 'storage', title: 'Storage & Backup', blurb: 'Cache usage, data folder, backups', icon: 'folders' },
  { id: 'security', title: 'Security', blurb: 'App login and the NSFW PIN (both optional)', icon: 'lock' },
  { id: 'system', title: 'System & network', blurb: 'Hardware, browser, speed test', icon: 'gauge' },
] as const
export type PanelId = (typeof PANELS)[number]['id']
export const isPanel = (v: unknown): v is PanelId => PANELS.some((p) => p.id === v)

export interface Checks {
  auth?: Obj
  preflight?: Obj
  icons?: Obj
  metadata?: Obj
  web?: Obj
  storage?: Obj
}
const DEP_LABELS: Record<string, string> = { telethon: 'Telethon', av: 'PyAV', pillow: 'Pillow', ffmpeg: 'FFmpeg', ffprobe: 'FFprobe' }

export function depsSummary(pre: Obj | undefined): string {
  if (!pre) return ''
  const missing = Object.keys(DEP_LABELS).filter((k) => !o(pre[k]).ok).map((k) => DEP_LABELS[k]!)
  return missing.length ? `${missing.length} missing: ${missing.join(', ')}` : 'All found'
}
export function iconsSummary(ic: Obj | undefined): string {
  if (!ic) return ''
  const entries = Object.entries(o(ic.icons))
  const missing = entries.filter(([, v]) => !v).map(([n]) => ({ vlc: 'VLC', mpc: 'MPC-HC', 'mpc-hc': 'MPC-HC', telegram: 'Telegram' })[n.toLowerCase() as 'vlc'] ?? n)
  return missing.length ? `${entries.length - missing.length} of ${entries.length} icons OK · ${missing.join(', ')} missing` : 'Icons OK'
}
export const telegramText = (auth: Obj | undefined) => (!auth ? 'Telegram status unavailable' : auth.connected ? `Telegram connected (saved session) — ${s(auth.status)}` : `Telegram not connected — ${s(auth.status) || 'log in below'}`)

export function statusRows(c: Checks, fmtBytes: (n: number) => string): StatusRow[] {
  const tgOk = Boolean(c.auth?.connected)
  const tgSub = tgOk ? 'Connected · session saved' : telegramText(c.auth).replace(/^Telegram /i, '') || 'Checking…'
  const deps = depsSummary(c.preflight)
  const depsOk = /all found/i.test(deps)
  const ic = iconsSummary(c.icons)
  const icOk = /^icons ok$/i.test(ic)
  const st = c.storage
  const db = st && Number.isFinite(Number(st.bytes)) ? `${fmtBytes(Number(st.bytes))} · ${Number(st.files ?? 0).toLocaleString()} files` : 'Local SQLite database'
  const webOk = Boolean(c.web) && !c.web!.default_login
  return [
    { icon: 'brand-telegram', title: 'Telegram', sub: tgSub, tone: tgOk ? 'ok' : 'warn', label: tgOk ? 'OK' : 'Check', go: 'telegram' },
    { icon: 'gauge', title: 'Dependencies', sub: deps || 'Checking…', tone: depsOk ? 'ok' : deps ? 'warn' : '', label: depsOk ? 'OK' : deps ? 'Check' : '…' },
    { icon: 'images', title: 'UI assets', sub: ic || 'Checking…', tone: icOk ? 'ok' : ic ? 'warn' : '', label: icOk ? 'OK' : ic ? 'Check' : '…' },
    { icon: 'database', title: 'Database', sub: db, tone: 'ok', label: 'OK' },
    { icon: 'lock', title: 'Web login', sub: webOk ? 'Custom login is set' : 'Still using the default password', tone: webOk ? 'ok' : 'warn', label: webOk ? 'OK' : 'Change', go: 'security' },
  ]
}
export interface Pill { label: string; ok: boolean }
export function pills(c: Checks): Pill[] {
  const metaOk = Boolean(c.metadata?.tpdb_token || c.metadata?.stashdb_api_key || c.metadata?.tpdb_configured || c.metadata?.stashdb_configured)
  const webOk = Boolean(c.web) && !c.web!.default_login
  return [
    { label: `Telegram · ${c.auth?.connected ? 'Connected' : 'Not connected'}`, ok: Boolean(c.auth?.connected) },
    { label: `Metadata · ${metaOk ? 'Provider configured' : 'No providers'}`, ok: metaOk },
    { label: `Web login · ${webOk ? 'Custom password' : 'Default password'}`, ok: webOk },
  ]
}
/** The toast text of "Validate configuration". */
export function validationMessage(r: { telegram?: Obj; metadata?: Obj; web?: Obj; preflight?: Obj }): string {
  const tgOk = Boolean(r.telegram?.configured)
  const metaOk = Boolean(r.metadata?.tpdb_token || r.metadata?.stashdb_api_key || r.metadata?.tpdb_configured || r.metadata?.stashdb_configured)
  const webOk = Boolean(r.web) && !r.web!.default_login
  const depOk = Boolean(o(r.preflight?.telethon).ok) && Boolean(o(r.preflight?.ffmpeg).ok)
  return [
    tgOk ? 'Telegram configuration is valid.' : 'Telegram API credentials are incomplete or unverified.',
    metaOk ? 'At least one metadata provider is configured.' : 'No metadata provider key is configured.',
    webOk ? 'Web login is customized.' : 'Web login still uses the default password or is unavailable.',
    depOk ? 'Media dependencies are ready.' : 'Media dependency checks need attention.',
  ].join(' ')
}

export const AUTONEXT_MODES = [['context', 'Current queue'], ['collection', 'Collection'], ['performer', 'Same performer'], ['studio', 'Same studio'], ['recommended', 'Recommended']] as const
export const NATIVE_MODES = [['off', 'Never by itself (a button on the video page)'], ['fallback', 'Only when the browser cannot play the video'], ['always', 'Always']] as const
export type NativeMode = (typeof NATIVE_MODES)[number][0]
export const DECODING_MODES = [['auto', 'Automatic (the graphics card when it can)'], ['hardware', 'Prefer the graphics card'], ['software', 'Processor only']] as const
export type DecodingMode = (typeof DECODING_MODES)[number][0]
export interface Experience {
  native_player: NativeMode
  video_decoding: DecodingMode
  autoplay: boolean
  autonext: boolean
  resume_prompt: boolean
  live_seek_preview: boolean
  partial_cache_prompt: boolean
  autonext_mode: string
}
/** Server → form, with the same defaults as the classic loader (resume and seek preview default on, partial-cache prompt off). */
export function experienceFrom(r: Obj | undefined): Experience {
  const x = r ?? {}
  const mode = s(x.autonext_mode)
  const native = s(x.native_player)
  return {
    native_player: NATIVE_MODES.some(([k]) => k === native) ? (native as NativeMode) : 'off',
    video_decoding: DECODING_MODES.some(([k]) => k === s(x.video_decoding)) ? (s(x.video_decoding) as DecodingMode) : 'auto',
    autoplay: Boolean(x.autoplay), autonext: Boolean(x.autonext), resume_prompt: x.resume_prompt !== false, live_seek_preview: x.live_seek_preview !== false,
    partial_cache_prompt: x.partial_cache_prompt === true, autonext_mode: AUTONEXT_MODES.some(([k]) => k === mode) ? mode : 'context',
  }
}

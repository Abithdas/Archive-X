/** Telegram panel rules, ported from the classic UI (settings, proxy form, QR login, phone login). */
type Obj = Record<string, unknown>
const s = (v: unknown) => String(v ?? '')

export type Tone = 'ok' | 'warn' | 'bad'
/** The line next to Save/Test: verified, saved, or not verified. */
export function apiStatus(r: Obj | undefined): { text: string; tone: Tone } {
  if (r?.api_verified) return { text: 'API credentials verified', tone: 'ok' }
  if (r?.configured) return { text: 'API credentials saved (account login may still be required)', tone: 'ok' }
  return { text: 'API credentials not verified', tone: 'warn' }
}
export const accountLine = (r: Obj | undefined) => (r?.connected ? 'Signed in · session saved on this computer' : r?.configured ? 'Not signed in · choose a sign-in method below' : 'Not signed in · add API credentials')

/** API ID must be present, and a hash must be typed or already saved (the server only returns a masked copy). */
export const credentialsReady = (apiId: string, hashTyped: string, maskedSaved: boolean) => Boolean(apiId.trim()) && (Boolean(hashTyped.trim()) || maskedSaved)

export type ProxyType = 'direct' | 'socks4' | 'socks5' | 'http' | 'mtproto'
export const PROXY_TYPES: ReadonlyArray<readonly [ProxyType, string]> = [['direct', 'Direct (no proxy)'], ['socks4', 'SOCKS4'], ['socks5', 'SOCKS5'], ['http', 'HTTP'], ['mtproto', 'MTProto']]
const TYPE_LABEL: Record<string, string> = { direct: 'Direct', socks4: 'SOCKS4', socks5: 'SOCKS5', http: 'HTTP', mtproto: 'MTProto' }
export const PORT_HINT: Record<ProxyType, string> = { direct: '1080', socks4: '1080', socks5: '1080', http: '8080', mtproto: '443' }
/** Which proxy fields apply to a type. */
export const proxyFields = (t: ProxyType) => ({ host: t !== 'direct', port: t !== 'direct', auth: t === 'socks4' || t === 'socks5' || t === 'http', secret: t === 'mtproto' })

export function proxyStatus(p: Obj | undefined): { label: string; help: string; locked: boolean } {
  const d = p ?? {}
  const type = s(d.type) || 'direct'
  const locked = d.source === 'environment'
  if (d.restart_required) return { label: 'Restart required', help: 'Proxy settings were saved. Restart Archive X to apply them to Telegram connections.', locked }
  if (locked && d.status === 'configured') return { label: 'Managed by environment', help: 'Archive X is using the runtime proxy setting. The endpoint and credentials are hidden.', locked }
  if (d.status === 'configured') return { label: `${TYPE_LABEL[type] ?? 'Proxy'} configured`, help: 'Archive X will use this proxy for Telegram connections. Provider reachability is not tested here.', locked }
  if (d.status === 'direct') return { label: 'Direct connection', help: 'No proxy is configured. Telegram uses this runtime’s default network route.', locked }
  if (d.status === 'invalid') return { label: 'Proxy setup needs attention', help: 'The saved proxy configuration is invalid. Review the selected type, server, port and required credentials.', locked }
  return { label: 'Status unavailable', help: 'Archive X could not read the proxy configuration. Reload Settings or check the app connection.', locked }
}
export interface ProxyForm { type: ProxyType; host: string; port: string; username: string; password: string; secret: string; clearAuth: boolean; clearSecret: boolean }
export const EMPTY_PROXY: ProxyForm = { type: 'direct', host: '', port: '', username: '', password: '', secret: '', clearAuth: false, clearSecret: false }
export function proxyForm(p: Obj | undefined): ProxyForm {
  const t = s(p?.type)
  return { ...EMPTY_PROXY, type: PROXY_TYPES.some(([k]) => k === t) ? (t as ProxyType) : 'direct', host: s(p?.host), port: s(p?.port) }
}
/** Blank credential fields mean "keep the saved one"; the server never sends them back. */
export const proxyPayload = (f: ProxyForm) => ({ type: f.type, host: f.host, port: f.port, username: f.username, password: f.password, secret: f.secret, clear_auth: f.clearAuth, clear_secret: f.clearSecret })
export const proxyPlaceholders = (p: Obj | undefined) => ({
  username: p?.username_saved ? 'Saved username — leave blank to keep' : 'Username (optional)',
  password: p?.password_saved ? 'Saved password — leave blank to keep' : 'Password (optional)',
  secret: p?.secret_saved ? 'Saved secret — leave blank to keep' : 'Paste the MTProto secret',
})

export type QrView =
  | { kind: 'connected'; message: string }
  | { kind: 'waiting'; message: string; image: string }
  | { kind: 'password'; message: string }
  | { kind: 'idle'; message: string; show: boolean; tone: 'info' | 'warning' }
  | { kind: 'error'; message: string }
  | { kind: 'info'; message: string }
/** QR login state → what the panel shows (classic applyQrState). */
export function qrView(state: Obj): QrView {
  const status = s(state.status || 'UNKNOWN')
  const msg = s(state.message)
  if (state.connected || status === 'AUTHORIZED') return { kind: 'connected', message: msg || 'Telegram connected.' }
  if (status === 'WAITING_FOR_SCAN') return { kind: 'waiting', message: msg || 'Waiting for scan…', image: s(state.qr_image) }
  if (status === 'PASSWORD_REQUIRED' || state.two_factor_required) return { kind: 'password', message: msg || 'QR accepted. Enter your Telegram Cloud Password to finish sign-in.' }
  if (['CANCELLED', 'IDLE', 'CONFIG_REQUIRED'].includes(status)) return { kind: 'idle', message: msg || 'Generate a short-lived code to connect your account.', show: status !== 'IDLE', tone: status === 'CANCELLED' ? 'info' : 'warning' }
  if (status === 'ERROR') return { kind: 'error', message: msg || 'QR login failed. Try again.' }
  return { kind: 'info', message: msg }
}

export const codeSent = (r: Obj) => Boolean(r.ok || r.phone_code_hash || r.status === 'CODE_SENT' || r.code_sent)
/** Result of verifying the SMS/app code: done, needs the cloud password, or failed with a reason. */
export function verifyOutcome(r: Obj): { kind: 'done' } | { kind: 'password' } | { kind: 'error'; message: string } {
  if (r.ok && r.two_factor_required) return { kind: 'password' }
  if (r.ok) return { kind: 'done' }
  return { kind: 'error', message: s(r.detail || r.error) || 'Verification failed' }
}
/** Spaces, dashes and brackets are removed; a number without "+" is rejected before anything is sent. */
export function normalizePhone(v: string): { phone: string; error: string } {
  const phone = v.replace(/[\s\-().]/g, '')
  if (!phone) return { phone, error: 'Enter your phone number (+countrycode).' }
  if (!/^\+\d{6,15}$/.test(phone)) return { phone, error: 'Use the international format, for example +14155550123.' }
  return { phone, error: '' }
}

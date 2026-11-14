/** System & network rules: hardware summary, internet speed result, and browser capability rows. Mirrors the classic page. */
type Obj = Record<string, unknown>
const s = (v: unknown) => (v == null ? '' : String(v))
const positive = (v: unknown) => Number.isFinite(Number(v)) && Number(v) > 0
const gb = (v: unknown) => (positive(v) ? `${Number(v)} GB` : 'Unavailable')

export interface Row { label: string; value: string }

/** CPU, GPU, RAM, storage, conversion backend and (when verified) hardware encoders. */
export function hardwareRows(r: Obj): Row[] {
  const cpu = (r.cpu ?? {}) as Obj, ram = (r.ram ?? {}) as Obj, storage = (r.storage ?? {}) as Obj
  const gpus = (Array.isArray(r.gpus) ? r.gpus : []) as Obj[]
  const gpu = gpus.map((g) => `${s(g.name) || 'GPU'}${Array.isArray(g.hardware_encoders) && g.hardware_encoders.length ? ` — ${g.hardware_encoders.join(', ')}` : ''}`).join(' | ') || 'No dedicated GPU detected'
  const encoders = Object.entries((r.verified_encoders ?? {}) as Record<string, unknown>).filter(([, v]) => v).map(([k]) => k).join(', ')
  const threads = `${s(cpu.physical_cores) || '?'} cores / ${s(cpu.logical_threads) || '?'} threads`
  const rows: Row[] = [
    { label: 'CPU', value: `${s(cpu.name) || 'Unknown'} · ${threads}` },
    { label: 'GPU', value: gpu },
    { label: 'RAM', value: `${gb(ram.total_gb)} · ${gb(ram.available_gb)} free` },
    { label: 'Storage', value: `${gb(storage.free_gb)} free` },
    { label: 'Conversion', value: `${s(r.conversion_backend) || 'libx264'}${encoders ? ' · verified' : ''}` },
  ]
  if (encoders) rows.push({ label: 'Hardware encoding', value: encoders })
  return rows
}

const mbps = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? `${v} Mbps` : 'Unavailable')
export interface SpeedResult { ok: boolean; status: string; rows: Row[]; note: string }
export function speedResult(r: Obj): SpeedResult {
  return {
    ok: Boolean(r.ok),
    status: r.ok ? 'Test complete' : 'No network result',
    rows: [
      { label: 'Download', value: mbps(r.download_mbps) },
      { label: 'Upload', value: mbps(r.upload_mbps) },
      { label: 'Latency', value: r.latency_ms == null ? 'Unavailable' : `${s(r.latency_ms)} ms` },
      { label: 'Route', value: s(r.quality) || 'unknown' },
    ],
    note: `Network mode: ${s(r.network_mode) || 'adaptive'} · Background capacity: ${r.background_capacity == null ? 'auto' : s(r.background_capacity)}${r.note ? ` · ${s(r.note)}` : ''}`,
  }
}

export interface BrowserInput { ua: string; platform: string; touchPoints: number; mp4: boolean; webm: boolean; hls: boolean; fetch: boolean; abort: boolean; pointer: boolean }
export function browserName(ua: string): string {
  const chromium = /Chrome|Chromium|Edg\//.test(ua) && !/OPR\//.test(ua)
  if (chromium) return /Edg\//.test(ua) ? 'Edge' : 'Chromium'
  if (/Firefox\//.test(ua)) return 'Firefox'
  if (/Safari\//.test(ua)) return 'Safari'
  return 'Other'
}
export function osName(ua: string, platform: string, touchPoints: number): string {
  if (/iPad|iPhone|iPod/.test(ua) || (platform === 'MacIntel' && touchPoints > 1)) return 'iOS/iPadOS'
  if (/Android/i.test(ua)) return 'Android'
  if (/Windows/i.test(ua)) return 'Windows'
  if (/Mac OS X/i.test(ua)) return 'macOS'
  if (/Linux/i.test(ua)) return 'Linux'
  return 'Other'
}
export function browserRows(i: BrowserInput): Row[] {
  return [
    { label: 'Browser', value: browserName(i.ua) }, { label: 'OS', value: osName(i.ua, i.platform, i.touchPoints) },
    { label: 'MP4 / H.264', value: i.mp4 ? 'Supported' : 'Fallback' }, { label: 'WebM', value: i.webm ? 'Supported' : 'Fallback' },
    { label: 'HLS', value: i.hls ? 'Native' : 'App fallback' }, { label: 'Fetch', value: i.fetch ? 'Available' : 'Limited' },
    { label: 'Abort', value: i.abort ? 'Available' : 'Limited' }, { label: 'Pointer input', value: i.pointer ? 'Available' : 'Fallback' },
  ]
}

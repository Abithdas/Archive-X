/** MP4 → Media Source Extensions: reads the file with Range requests, splits it into fragments with mp4box.js and feeds one SourceBuffer per track.
 *  Replaces the classic engine's MP4 pipeline, which put every track's initialisation segment into a single SourceBuffer (so any file with sound failed). */
import type { SmartOptions, SmartPipeline } from './smartstream'

export const AHEAD_SECONDS = 60          // stop reading when this much is buffered ahead of the playhead
export const KEEP_BEHIND_SECONDS = 30    // drop older data when the browser's buffer is under pressure
export const SAMPLES_PER_SEGMENT = 60
const SLICE = 256 * 1024                 // bytes handed to the parser at a time

export interface Mp4Track { id: number; codec: string; video?: unknown; audio?: unknown; type?: string; timescale?: number; nb_samples?: number }
export interface Mp4Info { tracks: Mp4Track[]; duration?: number; timescale?: number }

/** "video/mp4; codecs=…" for one track; the codec text mp4box reports is used as is (only "Opus" and "FLAC" need lower case). */
export function mimeFor(track: Pick<Mp4Track, 'codec' | 'video' | 'audio' | 'type'>): string {
  const kind = track.video || track.type === 'video' ? 'video' : 'audio'
  const codec = /^(opus|flac)$/i.test(track.codec) ? track.codec.toLowerCase() : track.codec
  return `${kind}/mp4; codecs="${codec}"`
}
/** Tracks the browser can play are kept; others (subtitles, data, unsupported codecs) are reported by name. */
export function planTracks(info: Mp4Info, supported: (mime: string) => boolean): { use: Array<{ track: Mp4Track; mime: string }>; unsupported: string[] } {
  const use: Array<{ track: Mp4Track; mime: string }> = [], unsupported: string[] = []
  for (const t of info.tracks) {
    if (!t.video && !t.audio && t.type !== 'video' && t.type !== 'audio') continue
    const mime = mimeFor(t)
    if (supported(mime)) use.push({ track: t, mime }); else unsupported.push(t.codec)
  }
  return { use, unsupported }
}
export const durationOf = (info: Mp4Info) => (info.duration && info.timescale ? info.duration / info.timescale : 0)
/** True when the playhead is covered by one of the buffered ranges (or sits within a fraction of a second of its end). */
export function isBuffered(ranges: ReadonlyArray<[number, number]>, t: number, slack = 0.25) { return ranges.some(([s, e]) => t >= s - 0.05 && t <= e + slack) }
export const bufferedAhead = (ranges: ReadonlyArray<[number, number]>, t: number) => { const r = ranges.find(([s, e]) => t >= s - 0.05 && t <= e + 0.25); return r ? Math.max(0, r[1] - t) : 0 }
/** Reading pauses while enough is buffered ahead, and while queued fragments are waiting to be appended. */
export const shouldPause = (ahead: number, queued: number) => ahead > AHEAD_SECONDS || queued > 24
/** mp4box asks for a different position (moov at the end of the file, or after a seek) when its return value differs from the next byte. */
export const needsJump = (next: number | undefined, position: number) => typeof next === 'number' && Number.isFinite(next) && next !== position
export const rangeHeader = (offset: number) => (offset > 0 ? { Range: `bytes=${offset}-` } : undefined)

/** Where to read next when the file puts its index (moov) after the media (mdat): the byte after the first mdat, or null when the index comes first, is unknown, or nothing follows. */
export function moovJump(head: Uint8Array, total: number): number | null {
  const dv = new DataView(head.buffer, head.byteOffset, head.byteLength)
  let pos = 0
  while (pos + 8 <= head.byteLength) {
    let size = dv.getUint32(pos)
    const type = String.fromCharCode(head[pos + 4]!, head[pos + 5]!, head[pos + 6]!, head[pos + 7]!)
    if (size === 1) { if (pos + 16 > head.byteLength) return null; size = Number(dv.getBigUint64(pos + 8)) }
    else if (size === 0) size = total - pos
    if (type === 'moov') return null
    if (size < 8) return null
    if (type === 'mdat') return pos + size < total ? pos + size : null
    pos += size
  }
  return null
}
export const totalSize = (res: { headers: { get(n: string): string | null } }) => {
  const cr = /\/(\d+)\s*$/.exec(res.headers.get('content-range') ?? '')
  return cr ? Number(cr[1]) : Number(res.headers.get('content-length') ?? 0)
}

const rangesOf = (b: TimeRanges): Array<[number, number]> => Array.from({ length: b.length }, (_, i) => [b.start(i), b.end(i)] as [number, number])
interface Track { id: number; sb: SourceBuffer; queue: ArrayBuffer[] }
type Mp4File = { onReady: (i: Mp4Info) => void; onSegment: (id: number, user: unknown, buf: ArrayBuffer, n: number, last: boolean) => void; onError: (e: unknown) => void; appendBuffer: (b: ArrayBuffer & { fileStart: number }) => number | undefined; flush: () => void; start: () => void; stop: () => void; seek: (t: number, useRap?: boolean) => { offset: number; time: number }; setSegmentOptions: (id: number, user: unknown, o: { nbSamples: number }) => void; initializeSegmentation: () => Array<{ id: number; user: unknown; buffer: ArrayBuffer }> }

/** Starts MSE playback of an MP4 on `video`. Returns a handle, or null when MediaSource or mp4box.js is missing. */
export function startMp4Stream(video: HTMLVideoElement, url: string, opts: SmartOptions = {}): SmartPipeline | null {
  const MP4Box = (window as unknown as { MP4Box?: { createFile: () => Mp4File } }).MP4Box
  const MS = (window as unknown as { MediaSource?: typeof MediaSource; ManagedMediaSource?: typeof MediaSource }).MediaSource ?? (window as unknown as { ManagedMediaSource?: typeof MediaSource }).ManagedMediaSource
  if (!MP4Box || !MS) return null
  const ms = new MS()
  const objectUrl = URL.createObjectURL(ms)
  const tracks = new Map<number, Track>()
  const file = MP4Box.createFile()
  let ctrl: AbortController | null = null
  let gen = 0                 // a new read (first, after a jump, after a seek) invalidates the old one
  let position = 0
  let ready = false
  let destroyed = false
  let wantStart = opts.startTime && opts.startTime > 0.5 ? opts.startTime : 0
  let tailMode = false         // the index sits after the media: it was read from the end, so samples must be read from their own offsets
  let probed = false

  const fail = (m: string) => { if (!destroyed) opts.onError?.(m) }
  const queued = () => [...tracks.values()].reduce((n, t) => n + t.queue.length, 0)
  const videoRanges = () => { const t = [...tracks.values()][0]; try { return t ? rangesOf(t.sb.buffered) : [] } catch { return [] } }
  const pump = (t: Track) => {
    if (destroyed || t.sb.updating || !t.queue.length || ms.readyState !== 'open') return
    const seg = t.queue.shift()!
    try { t.sb.appendBuffer(seg) } catch (e) {
      if ((e as DOMException).name === 'QuotaExceededError') {
        const cur = video.currentTime
        try { t.sb.remove(0, Math.max(0, cur - KEEP_BEHIND_SECONDS)) } catch { /* ignore */ }
        t.queue.unshift(seg)
      } else fail(`append failed: ${(e as Error).message}`)
    }
  }
  const finishIfDone = () => { if (!destroyed && ms.readyState === 'open' && fileDone && !queued() && ![...tracks.values()].some((t) => t.sb.updating)) { try { ms.endOfStream() } catch { /* ignore */ } } }
  let fileDone = false

  file.onError = (e) => fail(`MP4 parse error: ${String(e)}`)
  file.onReady = (info) => {
    if (destroyed) return
    const plan = planTracks(info, (m) => MS.isTypeSupported(m))
    if (!plan.use.length) return fail(`This browser cannot play the codec${plan.unsupported.length === 1 ? '' : 's'}: ${plan.unsupported.join(', ') || 'unknown'}`)
    try {
      for (const { track, mime } of plan.use) {
        const sb = ms.addSourceBuffer(mime)
        sb.mode = 'segments'
        const t: Track = { id: track.id, sb, queue: [] }
        sb.addEventListener('updateend', () => { pump(t); finishIfDone() })
        sb.addEventListener('error', () => fail('SourceBuffer error'))
        tracks.set(track.id, t)
        file.setSegmentOptions(track.id, t, { nbSamples: SAMPLES_PER_SEGMENT })
      }
      const d = durationOf(info)
      if (d > 0) ms.duration = d
      for (const init of file.initializeSegmentation()) { const t = tracks.get(init.id); if (t) { t.queue.push(init.buffer); pump(t) } }
    } catch (e) { return fail(`MSE setup failed: ${(e as Error).message}`) }
    ready = true
    if (wantStart > 0 || tailMode) { const s = file.seek(wantStart, true); wantStart = 0; tailMode = false; file.start(); void read(s.offset) } else file.start()
  }
  file.onSegment = (id, _user, buffer, _n, last) => {
    const t = tracks.get(id)
    if (!t || destroyed) return
    t.queue.push(buffer); pump(t)
    if (last) fileDone = true
  }

  /** Reads the file from `offset`, handing each chunk to mp4box. Pauses when enough is buffered, and jumps when mp4box asks for another position. */
  async function read(offset: number) {
    ctrl?.abort()
    const mine = ++gen
    ctrl = new AbortController()
    position = offset
    fileDone = false
    try {
      const res = await fetch(url, { signal: ctrl.signal, credentials: 'include', headers: rangeHeader(offset) })
      if (!res.ok && res.status !== 206) throw new Error(`Stream HTTP ${res.status}`)
      if (offset > 0 && res.status === 200) throw new Error('The server ignored the Range request')
      const reader = res.body?.getReader()
      if (!reader) throw new Error('No readable stream')
      let first = offset === 0 && !probed
      const total = totalSize(res)
      for (;;) {
        const { done, value } = await reader.read()
        if (done) { try { file.flush() } catch { /* ignore */ } if (mine === gen) { fileDone = true; finishIfDone() } return }
        // A network chunk can be large (some servers answer in one piece); it is fed in slices so pausing and jumping stay responsive.
        for (let at = 0; at < value.byteLength; at += SLICE) {
          while (!destroyed && mine === gen && ready && shouldPause(bufferedAhead(videoRanges(), video.currentTime), queued())) await new Promise((r) => setTimeout(r, 250))
          if (destroyed || mine !== gen) { void reader.cancel().catch(() => undefined); return }
          const part = value.subarray(at, Math.min(value.byteLength, at + SLICE))
          let tailJump: number | null = null
          if (first) { first = false; probed = true; tailJump = total > 0 ? moovJump(part, total) : null }
          const buf = part.slice().buffer as ArrayBuffer & { fileStart: number }
          buf.fileStart = position
          position += buf.byteLength
          opts.onProgress?.(position, 0)
          const next = file.appendBuffer(buf)
          if (tailJump !== null) { tailMode = true; void reader.cancel().catch(() => undefined); void read(tailJump); return }   // the index follows the media: it is read from the end of the file
          if (needsJump(next, position) && !(total > 0 && (next as number) >= total)) { void reader.cancel().catch(() => undefined); void read(next as number); return }
        }
      }
    } catch (e) { if (!destroyed && (e as Error).name !== 'AbortError') fail(`Fetch error: ${(e as Error).message}`) }
  }

  /** The viewer jumped somewhere not buffered yet: read from the nearest keyframe there. */
  const onSeeking = () => {
    if (!ready || destroyed) return
    const t = video.currentTime
    if (isBuffered(videoRanges(), t)) return
    try {
      file.stop()
      for (const tr of tracks.values()) tr.queue.length = 0
      const s = file.seek(t, true)
      file.start()
      void read(s.offset)
    } catch (e) { fail(`Seek failed: ${(e as Error).message}`) }
  }
  video.addEventListener('seeking', onSeeking)

  ms.addEventListener('sourceopen', () => { if (!destroyed) void read(0) }, { once: true })
  video.src = objectUrl

  const destroy = () => {
    if (destroyed) return
    destroyed = true
    gen++
    video.removeEventListener('seeking', onSeeking)
    try { ctrl?.abort() } catch { /* ignore */ }
    try { if (ms.readyState === 'open') ms.endOfStream() } catch { /* ignore */ }
    try { file.flush() } catch { /* ignore */ }
    try { video.removeAttribute('src'); video.load() } catch { /* ignore */ }
    URL.revokeObjectURL(objectUrl)
    tracks.clear()
  }
  return { destroy, seekTo: (t: number) => { video.currentTime = t } }
}

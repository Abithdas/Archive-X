import type { BufferStatus } from './PlayerControls'

export interface StreamSample { width: number; height: number; ahead: number; dropped: number; total: number; rate: number }
/** What the browser knows about the stream right now, read off the video element. */
export function sampleOf(v: HTMLVideoElement): StreamSample {
  const b = v.buffered
  const q = v.getVideoPlaybackQuality?.()
  return {
    width: v.videoWidth || 0, height: v.videoHeight || 0,
    ahead: b.length ? Math.max(0, b.end(b.length - 1) - v.currentTime) : 0,
    dropped: q?.droppedVideoFrames ?? 0, total: q?.totalVideoFrames ?? 0, rate: v.playbackRate || 1,
  }
}
/** What the server and the file say about the video, for the Video, Audio and Source parts of the stats. */
export interface StreamFacts { vcodec?: string; tenBit?: boolean; container?: string; acodec?: string; audio?: string; bitrate?: number; source?: string }
const upper = (v?: string) => (v ? v.toUpperCase() : '')
const mbps = (bps: number) => (bps >= 1e6 ? `${(bps / 1e6).toFixed(1)} Mb/s` : `${Math.round(bps / 1e3)} kb/s`)
/** The Buffer status rows, in parts like the stream stats of a streaming app: Video, Audio, Playback, Source. A part with nothing known is left out. */
export function statRows(percent: number, s: StreamSample | null, f: StreamFacts = {}): BufferStatus['rows'] {
  const rows: BufferStatus['rows'] = []
  const video: BufferStatus['rows'] = []
  if (s?.width) video.push({ k: 'Resolution', v: `${s.width} × ${s.height}` })
  if (f.vcodec) video.push({ k: 'Codec', v: `${upper(f.vcodec)} · ${f.tenBit ? '10-bit' : '8-bit'}` })
  if (f.bitrate && f.bitrate > 0) video.push({ k: 'Bitrate', v: mbps(f.bitrate) })
  if (f.container) video.push({ k: 'Container', v: f.container })
  if (video.length) rows.push({ k: '', v: '', head: 'Video' }, ...video)
  if (f.acodec || f.audio) rows.push({ k: '', v: '', head: 'Audio' }, { k: f.audio || 'Track', v: upper(f.acodec) || '–' })
  rows.push({ k: '', v: '', head: 'Playback' }, { k: 'Buffered', v: `${percent}%`, good: percent >= 99 }, { k: 'Ahead', v: s ? `${s.ahead >= 100 ? Math.round(s.ahead) : s.ahead.toFixed(1)} s` : '–' })
  if (s && s.total > 0) rows.push({ k: 'Dropped frames', v: `${s.dropped} of ${s.total}`, good: s.dropped === 0 })
  if (s && s.rate !== 1) rows.push({ k: 'Speed', v: `${s.rate}x` })
  rows.push({ k: 'Mode', v: 'Played in the app' })
  if (f.source) rows.push({ k: 'Source', v: f.source })
  return rows
}

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
/** The Buffer status rows: how much is loaded, how far ahead, the picture size, dropped frames, the speed. */
export function statRows(percent: number, s: StreamSample | null): BufferStatus['rows'] {
  const rows: BufferStatus['rows'] = [{ k: 'Buffered', v: `${percent}%`, good: percent >= 99 }, { k: 'Ahead', v: s ? `${s.ahead >= 100 ? Math.round(s.ahead) : s.ahead.toFixed(1)} s` : '–' }]
  if (s?.width) rows.push({ k: 'Picture', v: `${s.width}×${s.height}` })
  if (s && s.total > 0) rows.push({ k: 'Dropped frames', v: `${s.dropped} of ${s.total}`, good: s.dropped === 0 })
  if (s && s.rate !== 1) rows.push({ k: 'Speed', v: `${s.rate}x` })
  rows.push({ k: 'Playback', v: 'Direct' })
  return rows
}

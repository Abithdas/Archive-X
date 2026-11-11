import { describe, expect, it } from 'vitest'
import { bufferedAhead, durationOf, isBuffered, mimeFor, moovJump, needsJump, planTracks, rangeHeader, shouldPause, totalSize } from './mp4stream'

const box = (type: string, size: number) => { const b = new Uint8Array(size); new DataView(b.buffer).setUint32(0, size); b.set([...type].map((c) => c.charCodeAt(0)), 4); return b }
const cat = (...p: Uint8Array[]) => { const o = new Uint8Array(p.reduce((n, x) => n + x.length, 0)); let i = 0; for (const x of p) { o.set(x, i); i += x.length } return o }
describe('mp4 → MSE helpers', () => {
  it('builds a MIME per track and keeps only what the browser supports', () => {
    expect(mimeFor({ codec: 'vp09.01.11.08', video: {} })).toBe('video/mp4; codecs="vp09.01.11.08"'); expect(mimeFor({ codec: 'Opus', audio: {} })).toBe('audio/mp4; codecs="opus"'); expect(mimeFor({ codec: 'mp4a.40.2', type: 'audio' })).toBe('audio/mp4; codecs="mp4a.40.2"')
    const info = { tracks: [{ id: 1, codec: 'hvc1.1.6.L93', video: {} }, { id: 2, codec: 'mp4a.40.2', audio: {} }, { id: 3, codec: 'tx3g' }] }
    const plan = planTracks(info, (m) => m.startsWith('audio/'))
    expect(plan.use.map((u) => u.track.id)).toEqual([2]); expect(plan.unsupported).toEqual(['hvc1.1.6.L93'])
  })
  it('reads duration, buffered state and when to pause', () => {
    expect(durationOf({ tracks: [], duration: 9000, timescale: 600 })).toBe(15); expect(durationOf({ tracks: [] })).toBe(0)
    const r: Array<[number, number]> = [[0, 30], [100, 120]]
    expect(isBuffered(r, 10)).toBe(true); expect(isBuffered(r, 30.2)).toBe(true); expect(isBuffered(r, 60)).toBe(false); expect(isBuffered(r, 110)).toBe(true)
    expect(bufferedAhead(r, 10)).toBe(20); expect(bufferedAhead(r, 60)).toBe(0)
    expect(shouldPause(61, 0)).toBe(true); expect(shouldPause(10, 30)).toBe(true); expect(shouldPause(10, 3)).toBe(false)
  })
  it('knows when the parser wants another position, and builds Range headers', () => {
    expect(needsJump(undefined, 100)).toBe(false); expect(needsJump(100, 100)).toBe(false); expect(needsJump(5000, 100)).toBe(true); expect(needsJump(Number.NaN, 1)).toBe(false)
    expect(rangeHeader(0)).toBeUndefined(); expect(rangeHeader(2048)).toEqual({ Range: 'bytes=2048-' })
  })
  it('finds the index after the media, or none to look for', () => {
    expect(moovJump(cat(box('ftyp', 24), box('moov', 100), box('mdat', 500)), 624)).toBeNull()
    expect(moovJump(cat(box('ftyp', 24), box('free', 8), box('mdat', 5000)), 5200)).toBe(24 + 8 + 5000)
    expect(moovJump(cat(box('ftyp', 24), box('mdat', 5000)), 5024)).toBeNull()   // nothing follows
    expect(moovJump(cat(box('ftyp', 24)), 100000)).toBeNull(); expect(moovJump(new Uint8Array(4), 10)).toBeNull(); expect(moovJump(Uint8Array.from([0, 0, 0, 4, 102, 116, 121, 112]), 100)).toBeNull()
    const big = new Uint8Array(16); new DataView(big.buffer).setUint32(0, 1); big.set([...'mdat'].map((c) => c.charCodeAt(0)), 4); new DataView(big.buffer).setBigUint64(8, 9000n)
    expect(moovJump(cat(box('ftyp', 20), big), 10000)).toBe(20 + 9000)
  })
  it('gets the file size from the response', () => {
    const h = (o: Record<string, string>) => ({ headers: { get: (n: string) => o[n] ?? null } })
    expect(totalSize(h({ 'content-range': 'bytes 0-9/1234' }))).toBe(1234); expect(totalSize(h({ 'content-length': '999' }))).toBe(999); expect(totalSize(h({}))).toBe(0)
  })
})

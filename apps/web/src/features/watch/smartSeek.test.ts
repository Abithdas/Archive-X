import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { SmartApi } from './smartstream'

// The real in-browser remux code (vendor/smart-stream.js), driven with a fake MP4Box and a fake media source, to prove what a jump to an unread
// moment does: the old read stops, the file is asked where that moment starts, and the next read starts at that byte with a Range header.

type Fake = { onReady?: (i: unknown) => void; calls: string[]; seekOffset: number }
let box: Fake
let buffered: Array<[number, number]>
const fetches: Array<{ range?: string; aborted: boolean }> = []

class FakeSourceBuffer {
  mode = ''
  get buffered() { return { length: buffered.length, start: (i: number) => buffered[i]![0], end: (i: number) => buffered[i]![1] } }
  addEventListener() {}
  appendBuffer() {}
  remove() {}
}
class FakeMediaSource {
  static isTypeSupported = () => true
  readyState = 'open'
  private handlers: Record<string, () => void> = {}
  addEventListener(t: string, f: () => void) { this.handlers[t] = f; if (t === 'sourceopen') queueMicrotask(f) }
  addSourceBuffer() { return new FakeSourceBuffer() }
  endOfStream() {}
}

async function pipeline() {
  await import('./vendor/smart-stream.js')
  const api = (window as unknown as { ArchiveXSmartStream: SmartApi }).ArchiveXSmartStream
  const video = document.createElement('video')
  Object.defineProperty(video, 'currentTime', { value: 0, writable: true })
  const p = api.start(video, '/stream/-1/1', { name: 'a.mp4', vcodec: 'h264', acodec: 'aac', direct_likely: false }, {})!
  await new Promise((r) => setTimeout(r, 0))
  box.onReady?.({ tracks: [{ id: 1 }, { id: 2 }] })
  return p
}

beforeEach(() => {
  buffered = []
  fetches.length = 0
  box = { calls: [], seekOffset: 123456 }
  ;(window as unknown as { MP4Box: unknown }).MP4Box = {
    createFile: () => Object.assign(box, {
      setSegmentOptions() {}, initializeSegmentation: () => [{ buffer: new ArrayBuffer(1) }], start() { box.calls.push('start') }, stop() { box.calls.push('stop') },
      seek: (t: number) => { box.calls.push(`seek ${t}`); return { offset: box.seekOffset, time: t } }, appendBuffer() {}, flush() {},
    }),
  }
  ;(globalThis as unknown as { MediaSource: unknown }).MediaSource = FakeMediaSource
  URL.createObjectURL = () => 'blob:x'
  vi.stubGlobal('fetch', vi.fn(async (_u: string, init?: RequestInit) => {
    const rec = { range: (init?.headers as Record<string, string> | undefined)?.Range, aborted: false }
    fetches.push(rec)
    init?.signal?.addEventListener('abort', () => { rec.aborted = true })
    return { ok: true, status: rec.range ? 206 : 200, body: { getReader: () => ({ read: () => new Promise(() => {}), cancel() {} }) } }
  }))
})
afterEach(() => { vi.unstubAllGlobals() })

describe('in-browser remux: jumping to a moment that has not been read', () => {
  it('reads from the start first, with no range', async () => {
    await pipeline()
    expect(fetches).toHaveLength(1)
    expect(fetches[0]!.range).toBeUndefined()
  })
  it('stops the old read, asks the file where the moment starts, and reads from that byte', async () => {
    const p = await pipeline()
    p.seekTo!(600)
    expect(box.calls).toEqual(expect.arrayContaining(['stop', 'seek 600', 'start']))
    expect(fetches).toHaveLength(2)
    expect(fetches[0]!.aborted).toBe(true)
    expect(fetches[1]!.range).toBe('bytes=123456-')
  })
  it('leaves a moment that is already buffered to the browser', async () => {
    const p = await pipeline()
    buffered = [[0, 90]]
    p.seekTo!(30)
    expect(fetches).toHaveLength(1)
    expect(box.calls).not.toContain('seek 30')
  })
  it('remembers a jump asked for before the file was ready, and makes it once it is', async () => {
    await import('./vendor/smart-stream.js')
    const api = (window as unknown as { ArchiveXSmartStream: SmartApi }).ArchiveXSmartStream
    const video = document.createElement('video')
    const p = api.start(video, '/stream/-1/1', { name: 'a.mp4', vcodec: 'h264', acodec: 'aac', direct_likely: false }, {})!
    await new Promise((r) => setTimeout(r, 0))
    p.seekTo!(900)
    expect(fetches).toHaveLength(1)
    box.onReady?.({ tracks: [{ id: 1 }] })
    expect(box.calls).toContain('seek 900')
    expect(fetches[1]!.range).toBe('bytes=123456-')
  })
  it('reports a server that ignores the range instead of showing the wrong part', async () => {
    const errors: string[] = []
    await import('./vendor/smart-stream.js')
    const api = (window as unknown as { ArchiveXSmartStream: SmartApi }).ArchiveXSmartStream
    vi.stubGlobal('fetch', vi.fn(async (_u: string, init?: RequestInit) => ({ ok: true, status: 200, body: { getReader: () => ({ read: () => new Promise(() => {}), cancel() {} }) }, _r: init })))
    const p = api.start(document.createElement('video'), '/stream/-1/1', { name: 'a.mp4', vcodec: 'h264', acodec: 'aac', direct_likely: false }, { onError: (m: string) => errors.push(m) })!
    await new Promise((r) => setTimeout(r, 0))
    box.onReady?.({ tracks: [{ id: 1 }] })
    p.seekTo!(500)
    await new Promise((r) => setTimeout(r, 5))
    expect(errors.join(' ')).toMatch(/did not honour the range/)
  })
})

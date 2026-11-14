import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CAPTION_WINDOW, windowStart } from './tracks'
import { useTracks } from './useTracks'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn(), success: vi.fn(), loading: vi.fn(), dismiss: vi.fn() }) }))

/** A stand-in for the Python caption service (telegram_archive/playback/captions.py): one window at a time, the viewer's own window first,
 *  windows behind the viewer or more than one ahead are dropped, a running window is abandoned (checked every second, like between two
 *  segments) when the viewer has left it, and a window already transcribed answers at once (the disk cache).
 *  `loseEvery`: every n-th request is never answered, to see that the player gives up on it and asks again. */
function fakeServer(work: number, loseEvery = 0) {
  const done = new Set<number>()
  const shared = new Map<number, Promise<'ok' | 'skipped'>>()
  const askers = new Map<number, AbortSignal[]>()   // everyone waiting for a window: it is abandoned only when all of them have gone
  type Job = { start: number; seq: number; gone: () => boolean; settle: (r: 'ok' | 'skipped') => void }
  const queue: Job[] = []
  let running = false
  let pos: number | null = null
  let seq = 0
  let asked = 0
  const log: string[] = []
  const stale = (start: number) => pos !== null && (start < pos || start > pos + CAPTION_WINDOW)
  const rank = (j: Job) => [pos !== null && j.start === pos ? 0 : pos !== null && j.start === pos + CAPTION_WINDOW ? 1 : 2, Math.abs(j.start - (pos ?? j.start)), j.seq] as const
  const pump = () => {
    while (!running && queue.length) {
      for (const j of [...queue]) if (stale(j.start)) { queue.splice(queue.indexOf(j), 1); j.settle('skipped') }
      if (!queue.length) return
      queue.sort((a, b) => { const x = rank(a), y = rank(b); return x[0] - y[0] || x[1] - y[1] || x[2] - y[2] })
      const job = queue.shift()!
      if (job.gone()) { job.settle('skipped'); continue }
      running = true
      let elapsed = 0
      const step = setInterval(() => {
        elapsed += 1000
        if (stale(job.start) || (pos !== null && job.start !== pos && queue.some((j) => j.start === pos))) { clearInterval(step); running = false; job.settle('skipped'); pump(); return }   // left behind, or the viewer's own window is waiting
        if (elapsed >= work) { clearInterval(step); done.add(job.start); running = false; job.settle('ok'); pump() }
      }, 1000)
    }
  }
  const body = (start: number, r: 'ok' | 'skipped') => (r === 'ok' ? { start, cues: [{ start, end: start + CAPTION_WINDOW, text: `w${start}` }] } : { skipped: true, cues: [] })
  const handle = (url: string, signal?: AbortSignal): Promise<Response> => {
    const q = new URL(url, 'http://x').searchParams
    const start = windowStart(Number(q.get('start')))
    if (q.get('at') !== null) pos = windowStart(Number(q.get('at')))
    log.push(`ask ${start}`)
    asked++
    return new Promise<Response>((resolve, reject) => {
      const abort = () => reject(new DOMException('Aborted', 'AbortError'))
      if (signal?.aborted) return abort()
      signal?.addEventListener('abort', abort)
      if (signal) askers.set(start, [...(askers.get(start) ?? []), signal])
      if (loseEvery && asked % loseEvery === 0) return   // lost: never answered
      const answer = (r: 'ok' | 'skipped') => resolve(new Response(JSON.stringify(body(start, r)), { status: r === 'ok' ? 200 : 202 }))
      if (done.has(start)) return answer('ok')
      let p = shared.get(start)
      if (!p) {
        p = new Promise((settle) => { queue.push({ start, seq: seq++, gone: () => (askers.get(start) ?? []).every((x) => x.aborted), settle: (r) => { shared.delete(start); askers.delete(start); settle(r) } }); pump() })
        shared.set(start, p)
      }
      void p.then(answer)
    })
  }
  return { handle, log }
}

const json = (b: unknown) => Promise.resolve(new Response(JSON.stringify(b), { status: 200 }))
function rng(seed: number) { let a = seed >>> 0; return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296 } }

beforeEach(() => { vi.useFakeTimers(); localStorage.clear(); localStorage.setItem('archive-x:track-prefs', JSON.stringify({ sub: 'live' })) })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

/** Play, pause and skip at random, then stay where we are and see how long the caption for that place takes (ms; capped at `cap`). */
async function scenario(seed: number, work: number, cap: number, loseEvery = 0) {
  const server = fakeServer(work, loseEvery)
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    if (url.includes('/api/play/tracks/')) return json({ audio: [], subtitles: [], captions: { available: true, engine: 'x', install: '' } })
    if (url.includes('/api/captions/status')) return json({ model: 'ready', available: true })
    if (url.includes('/api/play/captions/')) return server.handle(url, init?.signal ?? undefined)
    return Promise.resolve(new Response('', { status: 404 }))
  }))
  const rnd = rng(seed)
  let t = Math.floor(rnd() * 300)
  let playing = true
  const view = renderHook((p: { time: number; playing: boolean }) => useTracks({ cid: '-1', mid: 1 }, { current: null }, p.time, p.playing), { initialProps: { time: t, playing } })
  const set = (time: number, play = playing) => { t = Math.max(0, time); playing = play; view.rerender({ time: t, playing }) }
  // Small steps, each in its own act(): React applies state updates when act ends, so one long step would hold back every retry.
  const wait = async (ms: number) => { for (let left = ms; left > 0; left -= 250) await act(async () => { await vi.advanceTimersByTimeAsync(Math.min(250, left)) }) }
  await wait(500)
  const ops: string[] = []
  for (let i = 0; i < 14; i++) {
    const r = rnd()
    if (r < 0.45) { const d = 1 + Math.floor(rnd() * 6); ops.push(`play ${d}s`); for (let s = 0; s < d; s++) { set(t + 1); await wait(1000) } }
    else if (r < 0.8) { const jump = (rnd() < 0.5 ? -1 : 1) * (3 + Math.floor(rnd() * 22)); ops.push(`skip ${jump}`); set(t + jump); await wait(Math.floor(rnd() * 2000)) }
    else if (r < 0.9) { ops.push(playing ? 'pause' : 'resume'); set(t, !playing); await wait(500) }
    else { const jump = (rnd() < 0.5 ? -1 : 1) * (60 + Math.floor(rnd() * 240)); ops.push(`big skip ${jump}`); set(t + jump); await wait(Math.floor(rnd() * 1500)) }
  }
  const want = `w${windowStart(t)}`
  let waited = 0
  while (view.result.current.text !== want && waited < cap) { await wait(250); waited += 250 }
  return { waited, ok: view.result.current.text === want, t, ops, asked: server.log.slice(-8) }
}

describe('live captions after skipping around', () => {
  it.each([[3_000], [12_000], [25_000]])('the caption for the place the viewer stopped at comes within one window of work (engine %i ms per window)', async (work) => {
    const slow: string[] = []
    for (let seed = 1; seed <= 40; seed++) {
      const r = await scenario(seed, work, 300_000)
      // The window being worked on when the viewer stops can only be abandoned between two segments (1 s here), then theirs is next.
      if (!r.ok || r.waited > work + 5_000) slow.push(`seed ${seed}: waited ${Math.round(r.waited / 1000)} s at ${r.t}; last asks ${r.asked.join(', ')}; ops ${r.ops.join(' | ')}`)
    }
    expect(slow.length, slow.slice(0, 3).join('\n')).toBe(0)
  }, 200_000)

  it('recovers from a request that is never answered (the player gives up after 90 s and asks again)', async () => {
    const bad: string[] = []
    for (let seed = 1; seed <= 25; seed++) {
      const r = await scenario(seed, 3_000, 300_000, 5)
      if (!r.ok || r.waited > 90_000 + 12_000) bad.push(`seed ${seed}: waited ${Math.round(r.waited / 1000)} s ok=${r.ok} at ${r.t}; ops ${r.ops.join(' | ')}`)
    }
    expect(bad.length, bad.slice(0, 3).join('\n')).toBe(0)
  }, 200_000)
})



import { describe, expect, it, vi, afterEach } from 'vitest'
import { ApiError } from '@/api/client'
import { splitEvents, streamChat, type ChatEvent } from './stream'

afterEach(() => vi.unstubAllGlobals())
const collect = async (it: AsyncGenerator<ChatEvent>) => { const out: ChatEvent[] = []; for await (const e of it) out.push(e); return out }
const chunked = (parts: string[]) => new Response(new ReadableStream<Uint8Array>({ start(c) { const enc = new TextEncoder(); parts.forEach((p) => c.enqueue(enc.encode(p))); c.close() } }), { status: 200 })

describe('splitEvents', () => {
  it('returns whole lines and keeps the unfinished one', () => {
    const r = splitEvents('{"t":"delta","text":"a"}\n{"t":"delta","text":"b"}\n{"t":"del')
    expect(r.events.map((e) => (e as { text: string }).text)).toEqual(['a', 'b']); expect(r.rest).toBe('{"t":"del')
  })
  it('skips blank and damaged lines instead of failing', () => {
    expect(splitEvents('\nnot json\n{"nope":1}\n{"t":"done","text":"x","model":"m","latency_ms":1,"tool_calls":0}\n').events).toHaveLength(1)
  })
})

describe('streamChat', () => {
  it('yields events even when a line is cut across network chunks', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => chunked(['{"t":"status","phase":"thinking","text":"Thin', 'king…"}\n{"t":"delta","te', 'xt":"Hi"}\n'])))
    const got = await collect(streamChat({ message: 'q', page: '/', selected: null }))
    expect(got).toEqual([{ t: 'status', phase: 'thinking', text: 'Thinking…' }, { t: 'delta', text: 'Hi' }])
  })
  it('reads a last line that has no newline at the end', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => chunked(['{"t":"delta","text":"end"}'])))
    expect(await collect(streamChat({ message: 'q', page: '/', selected: null }))).toEqual([{ t: 'delta', text: 'end' }])
  })
  it('turns a refusal into an ApiError with the server words', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"Message is required"}', { status: 400 })))
    await expect(collect(streamChat({ message: '', page: '/', selected: null }))).rejects.toMatchObject({ status: 400, detail: 'Message is required' })
    await expect(collect(streamChat({ message: '', page: '/', selected: null }))).rejects.toBeInstanceOf(ApiError)
  })
})

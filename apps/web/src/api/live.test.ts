import { describe, expect, it } from 'vitest'
import { REPORT_EVERY_MS, backoffMs, closeReport, parseStatusFrame } from './live'

describe('parseStatusFrame', () => {
  it('parses an object frame', () => expect(parseStatusFrame('{"version":"v1","current_job":null}')).toEqual({ version: 'v1', current_job: null }))
  it.each(['[1,2]', 'null', '"text"', '{broken', ''])('rejects %j', (frame) => expect(parseStatusFrame(frame)).toBeNull())
  it('ignores binary frames', () => expect(parseStatusFrame(new ArrayBuffer(4))).toBeNull())
})

describe('backoffMs', () => {
  it('grows exponentially and is capped', () => {
    expect(backoffMs(0)).toBe(500)
    expect(backoffMs(1)).toBe(1000)
    expect(backoffMs(3)).toBe(4000)
    expect(backoffMs(5)).toBe(15000)
    expect(backoffMs(50)).toBe(15000)
  })
})

describe('closeReport', () => {
  it('says the close code, how long the socket was up and the retry count', () => {
    const r = closeReport({ code: 4401, reason: '', wasClean: false }, 125_400, 2, true)
    expect(r).toMatchObject({ type: 'live.socket_closed', level: 'WARN', data: { code: 4401, open_seconds: 125, retry: 2, clean: false, browser_online: true } })
    expect(r.message).toBe('Live updates connection closed (code 4401); it was open for 125 s')
  })
  it('says when it never opened, and keeps the reason short', () => {
    const r = closeReport({ code: 1006, reason: 'x'.repeat(500), wasClean: false }, null, 0, false)
    expect(r.message).toContain('it never opened')
    expect(r.data.reason.length).toBe(120)
    expect(r.data.open_seconds).toBeNull()
  })
  it('reports at most once a minute', () => expect(REPORT_EVERY_MS).toBe(60_000))
})

describe('while the live connection is down', () => {
  it('asks for the status every few seconds instead, and stops once it is live', async () => {
    const { renderHook, act } = await import('@testing-library/react')
    const { QueryClient, QueryClientProvider } = await import('@tanstack/react-query')
    const { createElement } = await import('react')
    const { vi } = await import('vitest')
    const { POLL_WHILE_NOT_LIVE_MS, useLiveStatus } = await import('./live')
    vi.useFakeTimers()
    const sockets: Array<{ onopen: null | (() => void); close: () => void }> = []
    vi.stubGlobal('WebSocket', class { onopen: null | (() => void) = null; onclose = null; onmessage = null; onerror = null; close() {}; constructor() { sockets.push(this) } })
    const qc = new QueryClient()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    renderHook(() => useLiveStatus(true), { wrapper: ({ children }: { children: React.ReactNode }) => createElement(QueryClientProvider, { client: qc }, children) })
    act(() => { vi.advanceTimersByTime(POLL_WHILE_NOT_LIVE_MS * 2 + 10) })
    expect(spy).toHaveBeenCalledTimes(2)
    act(() => { sockets[0]!.onopen?.() })                              // the socket comes up: no more polling
    spy.mockClear()
    act(() => { vi.advanceTimersByTime(POLL_WHILE_NOT_LIVE_MS * 3) })
    expect(spy).not.toHaveBeenCalled()
    vi.useRealTimers(); vi.unstubAllGlobals()
  })
})

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

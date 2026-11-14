import { describe, expect, it } from 'vitest'
import { backoffMs, parseStatusFrame } from './live'

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

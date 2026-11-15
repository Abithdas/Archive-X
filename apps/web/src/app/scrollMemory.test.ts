import { describe, expect, it } from 'vitest'
import { entryKey, recalledScroll, rememberScroll } from './scrollMemory'

describe('scroll memory', () => {
  it('tells history entries apart by the router key, falling back to the address', () => {
    expect(entryKey({ href: '/performers', state: { __TSR_key: 'abc' } })).toBe('abc')
    expect(entryKey({ href: '/performers', state: {} })).toBe('/performers')
  })
  it('gives back what was stored, and zero for a page never seen', () => {
    rememberScroll('k1', 640)
    expect(recalledScroll('k1')).toBe(640)
    expect(recalledScroll('never')).toBe(0)
  })
  it('forgets the oldest entries instead of growing without end', () => {
    for (let i = 0; i < 120; i++) rememberScroll(`e${i}`, i + 1)
    expect(recalledScroll('e0')).toBe(0)
    expect(recalledScroll('e119')).toBe(120)
  })
})

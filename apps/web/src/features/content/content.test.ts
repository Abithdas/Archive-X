import { describe, expect, it } from 'vitest'
import { askFor, channelsOfMode, emptyHint, otherMode, pinProblem, switchBody } from './content'

describe('content mode logic', () => {
  it('asks nothing towards SFW, the PIN into NSFW, and a new PIN when none exists', () => {
    expect(askFor('sfw', { pin_set: true })).toBe('none')
    expect(askFor('nsfw', { pin_set: true })).toBe('pin')
    expect(askFor('nsfw', { pin_set: false })).toBe('create')
  })
  it('sends the PIN the way the server wants it', () => {
    expect(switchBody('nsfw', 'pin', '2468')).toEqual({ mode: 'nsfw', pin: '2468' })
    expect(switchBody('nsfw', 'create', '2468')).toEqual({ mode: 'nsfw', new_pin: '2468' })
    expect(switchBody('sfw', 'none', '')).toEqual({ mode: 'sfw' })
  })
  it('accepts 4 to 12 digits only', () => {
    expect(pinProblem('1234')).toBeNull()
    expect(pinProblem('123')).not.toBeNull()
    expect(pinProblem('12a4')).not.toBeNull()
    expect(pinProblem('1'.repeat(13))).not.toBeNull()
  })
  it('says so when no channel is in the library for the mode', () => {
    const ch = [{ in_library: 1, content_rating: 'nsfw' as const }, { in_library: 0, content_rating: 'sfw' as const }]
    expect(emptyHint('sfw', ch)).toContain('SFW')
    expect(emptyHint('nsfw', ch)).toBeNull()
    expect(otherMode('sfw')).toBe('nsfw')
  })
  it('lists the channels of a mode that are in the library', () => {
    const base = { telegram_id: '1', title: 'a', username: null, holds: 'both' as const, videos: 0, photos: 0 }
    expect(channelsOfMode([{ ...base, in_library: 1, content_rating: 'sfw' }, { ...base, telegram_id: '2', in_library: 0, content_rating: 'sfw' }], 'sfw')).toHaveLength(1)
  })
})

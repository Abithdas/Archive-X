import { beforeEach, describe, expect, it } from 'vitest'
import { DEFAULT_PREFS, loadPrefs, normalizePrefs, savePrefs } from './playerPrefs'

describe('what the player remembers', () => {
  beforeEach(() => localStorage.clear())
  it('starts at full volume, unmuted, normal speed', () => expect(loadPrefs()).toEqual(DEFAULT_PREFS))
  it('keeps the volume, mute and speed from one video to the next', () => {
    savePrefs({ volume: 0.4 }); savePrefs({ muted: true }); savePrefs({ rate: 1.5 })
    expect(loadPrefs()).toEqual({ volume: 0.4, muted: true, rate: 1.5 })
  })
  it('ignores values that make no sense', () => expect(normalizePrefs({ volume: 9, rate: 7 })).toEqual({ volume: 1, muted: false, rate: 1 }))
})

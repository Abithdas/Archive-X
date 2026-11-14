import { describe, expect, it } from 'vitest'
import { initialMode, isDecodeFailure, shouldFallBack, wantsSmart } from './smartstream'

const yes = { canPlay: () => true }
const no = { canPlay: () => false }
describe('smart playback decisions', () => {
  it('recognises decode failures but not network ones', () => {
    expect(isDecodeFailure(3)).toBe(true); expect(isDecodeFailure(4)).toBe(true); expect(isDecodeFailure(2)).toBe(false); expect(isDecodeFailure(0)).toBe(false)
    expect(isDecodeFailure(0, 'Cannot decode this')).toBe(true); expect(isDecodeFailure(0, 'unsupported codec hevc')).toBe(true); expect(isDecodeFailure(0, 'timeout')).toBe(false)
  })
  it('goes smart only for files that will not play directly and the engine can handle', () => {
    expect(wantsSmart({ direct_likely: false }, yes)).toBe(true); expect(wantsSmart({ direct_likely: true }, yes)).toBe(false); expect(wantsSmart({ direct_likely: false }, no)).toBe(false)
    expect(wantsSmart(null, yes)).toBe(false); expect(wantsSmart({}, null)).toBe(false)
    expect(initialMode({ direct_likely: false }, yes)).toBe('mse'); expect(initialMode(undefined, yes)).toBe('direct'); expect(initialMode({ direct_likely: false }, no)).toBe('direct')
  })
  it('falls back once, only from direct play, after a decode failure', () => {
    const info = { direct_likely: false }
    expect(shouldFallBack('direct', false, 3, info, yes)).toBe(true)
    expect(shouldFallBack('direct', true, 3, info, yes)).toBe(false); expect(shouldFallBack('mse', false, 3, info, yes)).toBe(false)
    expect(shouldFallBack('direct', false, 2, info, yes)).toBe(false); expect(shouldFallBack('direct', false, 3, info, no)).toBe(false); expect(shouldFallBack('direct', false, 3, { direct_likely: true }, yes)).toBe(false)
  })
})

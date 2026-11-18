import { describe, expect, it } from 'vitest'
import { withFavorite } from './favorite'

describe('withFavorite', () => {
  const page = { total: 2, items: [{ id: 1, media_id: 10, favorite: false, name: 'a' }, { id: 2, media_id: 11, favorite: false, name: 'b' }] }
  it('flips only the row of that video, in a library page', () => {
    const out = withFavorite(page, 10, true)
    expect(out.items[0]!.favorite).toBe(true)
    expect(out.items[1]).toBe(page.items[1])           // untouched rows keep their identity
    expect(page.items[0]!.favorite).toBe(false)        // the original is not mutated
  })
  it('works on a single video and on a performer\'s nested videos', () => {
    expect(withFavorite({ media_id: 5, favorite: true }, 5, false)).toEqual({ media_id: 5, favorite: false })
    const perf = { performer: { id: 1 }, videos: [{ media_id: 7, favorite: false }] }
    expect(withFavorite(perf, 7, true).videos[0]!.favorite).toBe(true)
  })
  it('returns the very same object when nothing matches or it is already set', () => {
    expect(withFavorite(page, 99, true)).toBe(page)
    expect(withFavorite({ media_id: 5, favorite: true }, 5, true)).toEqual({ media_id: 5, favorite: true })
    expect(withFavorite(null, 1, true)).toBeNull()
    expect(withFavorite('x', 1, true)).toBe('x')
  })
})

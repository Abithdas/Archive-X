import { describe, expect, it } from 'vitest'
import { countLabel } from './countLabel'

describe('the count on a cast or studio card', () => {
  it('counts videos in the adult mode', () => expect(countLabel({ video_count: 3, movies: 1, series: 1 }, false)).toBe('3 videos'))
  it('counts movies and series in SFW, never episodes', () => {
    expect(countLabel({ video_count: 40, movies: 2, series: 3 }, true)).toBe('2 movies · 3 series')
    expect(countLabel({ video_count: 12, movies: 0, series: 1 }, true)).toBe('1 series')
    expect(countLabel({ video_count: 1, movies: 1, series: 0 }, true)).toBe('1 movie')
  })
  it('falls back to videos while no title is known', () => expect(countLabel({ video_count: 1 }, true)).toBe('1 video'))
})

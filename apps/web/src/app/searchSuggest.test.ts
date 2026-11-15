import { describe, expect, it } from 'vitest'
import type { Entity } from '@/api/types'
import { highlightParts, suggest, suggestionPath } from './searchSuggest'

const e = (id: number, name: string, video_count: number, image: string | null = null): Entity => ({ id, name, video_count, image })

describe('suggest', () => {
  const perfs = [e(1, 'Ava Stone', 40), e(2, 'Mia Avalon', 90), e(3, 'Zoe Lane', 5)]
  const studios = [e(7, 'Avant Studio', 12), e(8, 'Blue Door', 300)]
  it('gives nothing for an empty query', () => expect(suggest(perfs, studios, '  ')).toEqual([]))
  it('puts names that start with the query first, then the busiest, across performers and studios', () => {
    expect(suggest(perfs, studios, 'ava').map((s) => s.title)).toEqual(['Ava Stone', 'Avant Studio', 'Mia Avalon'])
  })
  it('matches the way the lists do: any order, no accents, no case', () => {
    expect(suggest([e(1, 'Zoë Lane', 3)], [], 'lane zoe').map((s) => s.title)).toEqual(['Zoë Lane'])
  })
  it('says what each result is, with its picture and where it goes', () => {
    const [a] = suggest(perfs, studios, 'ava stone')
    expect(a).toMatchObject({ id: 'performers:1', title: 'Ava Stone', description: 'Performer · 40 videos' })
    expect(a!.image).toBe('/api/performers/1/image?w=96&v=')
    expect(suggestionPath(a!)).toBe('/performers/1')
    expect(suggest([], [e(8, 'Blue Door', 1)], 'blue')[0]).toMatchObject({ description: 'Studio · 1 video', id: 'studios:8' })
  })
  it('stops at the limit', () => expect(suggest(perfs, studios, 'a', 2)).toHaveLength(2))
})

describe('highlightParts', () => {
  it('marks the matching letters and keeps the rest', () => {
    expect(highlightParts('Ava Stone', 'sto')).toEqual([{ text: 'Ava ', match: false }, { text: 'Sto', match: true }, { text: 'ne', match: false }])
  })
  it('ignores case and shows plain text when nothing matches or there is no query', () => {
    expect(highlightParts('Ava Stone', 'AVA')[0]).toEqual({ text: 'Ava', match: true })
    expect(highlightParts('Ava Stone', 'zzz')).toEqual([{ text: 'Ava Stone', match: false }])
    expect(highlightParts('Ava Stone', '')).toEqual([{ text: 'Ava Stone', match: false }])
  })
  it('finds a match at the very end', () => expect(highlightParts('Ava Stone', 'one').at(-1)).toEqual({ text: 'one', match: true }))
})

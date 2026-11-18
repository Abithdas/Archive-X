import { describe, expect, it } from 'vitest'
import { originFor } from './origin'

describe('originFor', () => {
  it('leads back to Photos or People when that is where the person came from', () => {
    expect(originFor('performers', '/photos')).toEqual({ to: '/photos', label: 'Photos' })
    expect(originFor('performers', '/people')).toEqual({ to: '/people', label: 'People' })
  })
  it('keeps the list for its own pages and unknown places', () => {
    expect(originFor('performers', '/performers')).toBeNull()
    expect(originFor('performers', '/performers/4')).toBeNull()
    expect(originFor('performers', '')).toBeNull()
    expect(originFor('studios', '/watch/1/2')).toEqual({ to: '/videos', label: 'Videos' })
  })
})

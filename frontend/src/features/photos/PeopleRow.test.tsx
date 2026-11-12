import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { PeopleRow } from './PeopleRow'
import { bestGuess, peopleRow } from './faces'
import { usePhotoStore } from './store'

const G = (id: number, o = {}) => ({ id, label: `Face group ${id}`, performer_id: null, performer_name: '', photo_count: 5, face_count: 5, cluster_ids: [id], detection_ids: [id], channel_ids: ['-1'], representative_url: `/c/${id}`, ...o })
beforeEach(() => usePhotoStore.setState({ face: null, tab: 'library' }))

describe('people row', () => {
  it('lists named people first, then frequent faces, and skips one-offs', () => {
    const rows = peopleRow([G(1), G(2, { photo_count: 1 }), G(3, { performer_id: 9, performer_name: 'Ava Stone', photo_count: 2 }), G(4, { photo_count: 9 })])
    expect(rows.map((g) => g.id)).toEqual([3, 4, 1])
  })
  it('picks the best candidate to ask "Is this X?" about', () => {
    expect(bestGuess(G(1, { profile_image_suggestions: [{ id: 5, name: 'Mia', similarity_score: 0.8 }], confirmed_assignment_suggestions: [{ id: 6, name: 'Zoe', similarity_score: 0.5 }] }))?.name).toBe('Mia')
    expect(bestGuess(G(1, { performer_id: 3 }))).toBeNull()
  })
  it('filters the library to a person on click and clears on a second click', () => {
    render(<PeopleRow groups={[G(3, { performer_id: 9, performer_name: 'Ava Stone' }), G(4, { profile_image_suggestions: [{ id: 5, name: 'Mia Rivera', similarity_score: 0.9 }] })]} />)
    expect(screen.getByText('Is this Mia Rivera?')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Ava Stone/ }))
    expect(usePhotoStore.getState().face?.performerId).toBe(9)
    fireEvent.click(screen.getByRole('button', { name: /Ava Stone/ }))
    expect(usePhotoStore.getState().face).toBeNull()
  })
  it('renders nothing without people', () => { const { container } = render(<PeopleRow groups={[]} />); expect(container).toBeEmptyDOMElement() })
})

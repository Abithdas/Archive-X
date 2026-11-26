import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ReviewsButton, ScoreBadges, scoreTone, type ReviewsData } from './Reviews'

const DATA: ReviewsData = { ok: true, scores: [{ source: 'Rotten Tomatoes', value: '92%' }, { source: 'Metacritic', value: '79' }], summary: { text: 'Critics praised the visuals.', title: 'Dune: Part Two', url: 'https://en.wikipedia.org/wiki/Dune:_Part_Two' } }

describe('critics scores', () => {
  it('shows each score as a small mark beside the rating, named for screen readers', () => {
    render(<p><ScoreBadges data={DATA} /></p>)
    expect(screen.getByLabelText('Rotten Tomatoes 92%')).toHaveTextContent('RT92%')
    expect(screen.getByLabelText('Metacritic 79')).toHaveTextContent('MC79')
  })
  it('shows nothing when there are no scores, or no answer, or a broken one', () => {
    const { container, rerender } = render(<p><ScoreBadges data={{ ok: true, scores: [], summary: null }} /></p>)
    expect(container.querySelector('[aria-label]')).toBeNull()
    rerender(<p><ScoreBadges data={undefined} /></p>)
    expect(container.querySelector('[aria-label]')).toBeNull()
    rerender(<p><ScoreBadges data={{} as ReviewsData} /></p>)
    expect(container.querySelector('[aria-label]')).toBeNull()
  })
  it('colours the way the sites do', () => {
    expect([scoreTone('Metacritic', '79'), scoreTone('Metacritic', '50'), scoreTone('Metacritic', '20'), scoreTone('Rotten Tomatoes', '92%'), scoreTone('Rotten Tomatoes', '30%'), scoreTone('Metacritic', 'n/a')]).toEqual(['good', 'mixed', 'bad', 'good', 'bad', 'mixed'])
  })
})

describe('the Reviews button', () => {
  beforeAllDialog()
  it('opens the scores and what critics said, with a link to read the rest', () => {
    render(<ReviewsButton data={DATA} title="Dune" className="b" />)
    fireEvent.click(screen.getByRole('button', { name: 'Reviews' }))
    const dialog = screen.getByRole('dialog')
    const scores = within(dialog).getByRole('list', { name: "Critics' scores" })
    expect(within(scores).getByText('92%')).toBeInTheDocument()
    expect(within(scores).getByText('Metacritic')).toBeInTheDocument()
    expect(within(dialog).getByText('Critics praised the visuals.')).toBeInTheDocument()
    const link = within(dialog).getByRole('link', { name: /Read more in “Dune: Part Two” on Wikipedia/ })
    expect(link).toHaveAttribute('href', 'https://en.wikipedia.org/wiki/Dune:_Part_Two')
    expect(link).toHaveAttribute('target', '_blank')
  })
  it('is not there when there is nothing to show, and works with scores only or a summary only', () => {
    const { container, rerender } = render(<ReviewsButton data={{ ok: true, scores: [], summary: null }} title="X" className="b" />)
    expect(container.querySelector('button')).toBeNull()
    rerender(<ReviewsButton data={undefined} title="X" className="b" />)
    expect(container.querySelector('button')).toBeNull()
    rerender(<ReviewsButton data={{ ok: true, scores: [{ source: 'Metacritic', value: '55' }], summary: null }} title="X" className="b" />)
    fireEvent.click(screen.getByRole('button', { name: 'Reviews' }))
    expect(screen.queryByText('What critics said')).toBeNull()
    expect(screen.getByText('55')).toBeInTheDocument()
  })
})

function beforeAllDialog() {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
}

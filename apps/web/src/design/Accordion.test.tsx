import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Disclosure } from './Accordion'

describe('Disclosure', () => {
  it('starts folded, opens and folds on click, and reports it', () => {
    const seen = vi.fn()
    render(<Disclosure summary="More" onToggle={seen}><p>Hidden text</p></Disclosure>)
    const head = screen.getByRole('button', { name: 'More' })
    expect(head).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByText('Hidden text').closest('[inert]')).not.toBeNull()
    fireEvent.click(head)
    expect(head).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Hidden text').closest('[inert]')).toBeNull()
    expect(head.closest('.mo-fold')).toHaveAttribute('data-open', 'true')
    fireEvent.click(head)
    expect(seen.mock.calls.map((c) => c[0])).toEqual([true, false])
  })
  it('can start open', () => {
    render(<Disclosure summary="Open now" defaultOpen><p>Body</p></Disclosure>)
    expect(screen.getByRole('button', { name: 'Open now' })).toHaveAttribute('aria-expanded', 'true')
  })
})

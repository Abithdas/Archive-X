import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MenuSection } from './PlayerParts'

describe('a folding part of a player menu', () => {
  it('starts folded, opens and folds again from its heading', () => {
    render(<MenuSection title="Picture and sound"><span>Brightness</span></MenuSection>)
    const head = screen.getByRole('button', { name: /Picture and sound/ })
    expect(head).toHaveAttribute('aria-expanded', 'false'); expect(screen.queryByText('Brightness')).toBeNull()
    fireEvent.click(head); expect(screen.getByText('Brightness')).toBeInTheDocument()
    fireEvent.click(head); expect(screen.queryByText('Brightness')).toBeNull()
  })
  it('can start open', () => {
    render(<MenuSection title="Caption style" defaultOpen><span>Size</span></MenuSection>)
    expect(screen.getByText('Size')).toBeInTheDocument()
  })
})

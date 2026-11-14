import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Check } from './parts'

describe('Check', () => {
  it('keeps the hidden channel input inside its own row', () => {
    // The input is sr-only (position: absolute). Without a positioned row it lands against a far ancestor, so every row below a
    // scrolling list's visible part made the whole window scroll once a channel was ticked.
    render(<Check checked={false} onChange={vi.fn()} title="Main" icon={<span>M</span>} />)
    const row = screen.getByRole('checkbox').closest('label')
    expect(row).toHaveClass('relative')
  })
})

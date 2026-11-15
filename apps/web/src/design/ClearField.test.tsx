import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { ClearField } from './ClearField'

function Harness({ start = '' }: { start?: string }) {
  const [v, setV] = useState(start)
  return <ClearField value={v} onChange={setV} placeholder="Find…" label="Find" clearLabel="Clear find" />
}

describe('ClearField', () => {
  it('types through the real input and draws each letter on top', () => {
    const { container } = render(<Harness />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Find' }), { target: { value: 'abc' } })
    expect(screen.getByRole('textbox', { name: 'Find' })).toHaveValue('abc')
    expect(container.querySelectorAll('.ax-char')).toHaveLength(3)
    expect(container.querySelector('.t-clear')).toHaveClass('has-value')
  })
  it('has no clear button while empty, and the button empties the box', () => {
    render(<Harness start="hello" />)
    fireEvent.click(screen.getByRole('button', { name: 'Clear find' }))
    expect(screen.getByRole('textbox', { name: 'Find' })).toHaveValue('')
    expect(screen.queryByRole('button', { name: 'Clear find' })).toBeNull()
  })
})

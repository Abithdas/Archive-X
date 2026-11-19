import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { IntegrationsPanel } from './IntegrationsPanel'

describe('Settings, Integrations', () => {
  it('offers the calendar file for the chosen range', () => {
    render(<IntegrationsPanel />)
    const link = screen.getByRole('link', { name: 'Download calendar file' })
    expect(link).toHaveAttribute('href', '/api/screen/calendar.ics?days=90')
    fireEvent.change(screen.getByLabelText('How far ahead'), { target: { value: '30' } })
    expect(link).toHaveAttribute('href', '/api/screen/calendar.ics?days=30')
  })
})

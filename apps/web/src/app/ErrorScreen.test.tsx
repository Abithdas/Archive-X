import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ErrorBoundary, ErrorScreen, isStaleChunk } from './ErrorScreen'
import { clearErrors, record, recentErrors } from './errorLog'
import { buildReport } from './report'

const toast = vi.hoisted(() => Object.assign(vi.fn(), { error: vi.fn() }))
vi.mock('sonner', () => ({ toast }))
afterEach(() => { clearErrors(); toast.mockClear(); toast.error.mockClear() })

function Boom({ when }: { when: boolean }) { if (when) throw new Error('kaboom'); return <p>fine</p> }

describe('buildReport', () => {
  it('has the error, page without query, browser and recent errors', () => {
    const r = buildReport({ error: new Error('bad thing'), url: 'https://x.test:8443/photos?token=secret#frag', userAgent: 'UA/1', recent: [{ at: 't1', message: 'earlier' }], now: new Date('2026-01-01T00:00:00Z') })
    expect(r).toContain('Page: https://x.test:8443/photos\n')
    expect(r).not.toContain('secret')
    expect(r).toContain('Error: Error: bad thing')
    expect(r).toContain('Browser: UA/1')
    expect(r).toContain('- t1 earlier')
  })
  it('copes with a thrown string', () => { expect(buildReport({ error: 'oops', url: 'bad url', userAgent: '', recent: [] })).toContain('oops') })
})

describe('errorLog', () => {
  it('keeps only the latest 20', () => { for (let i = 0; i < 25; i++) record(`e${i}`); expect(recentErrors()).toHaveLength(20); expect(recentErrors()[0]!.message).toBe('e5') })
})

describe('ErrorBoundary', () => {
  it('shows the screen, then renders the page again after Try again', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    let broken = true
    const View = () => <Boom when={broken} />
    render(<ErrorBoundary><View /></ErrorBoundary>)
    expect(screen.getByRole('alert')).toHaveTextContent('Something went wrong')
    expect(screen.getByText('kaboom')).toBeInTheDocument()
    broken = false
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(screen.getByText('fine')).toBeInTheDocument()
  })
  it('clears itself when the page changes', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const { rerender } = render(<ErrorBoundary resetKey="/a"><Boom when /></ErrorBoundary>)
    expect(screen.getByRole('alert')).toBeInTheDocument()
    rerender(<ErrorBoundary resetKey="/b"><Boom when={false} /></ErrorBoundary>)
    expect(screen.getByText('fine')).toBeInTheDocument()
  })
})

describe('ErrorScreen', () => {
  it('copies the report to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<ErrorScreen error={new Error('x')} />)
    fireEvent.click(screen.getByRole('button', { name: 'Copy report' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(expect.stringContaining('Archive X problem report')))
    expect(toast).toHaveBeenCalledWith('Report copied')
  })
})

describe('isStaleChunk', () => {
  it('recognises a page file that failed to load, in the browsers\' wordings', () => {
    expect(isStaleChunk(new Error('Failed to fetch dynamically imported module: https://x/assets/A.js'))).toBe(true)
    expect(isStaleChunk(new Error('Importing a module script failed.'))).toBe(true)
    expect(isStaleChunk(new Error('Cannot read properties of undefined'))).toBe(false)
  })
})

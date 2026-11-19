import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { NotificationsPage } from './NotificationsPage'
import { useCleared } from './NotificationsPanel'

afterEach(() => { vi.unstubAllGlobals(); useCleared.setState({ gone: [] }); try { localStorage.clear() } catch { /* none */ } })
const now = () => Math.floor(Date.now() / 1000)
const status = {
  events: [
    { level: 'ERROR', event: 'telegram.send.failed', message: 'Telegram send failed for video 1', created_at: now() - 60 },
    { level: 'SUCCESS', event: 'backup.verified', message: 'Backup verified', created_at: now() - 120 },
  ],
  jobs: [],
}
const mount = () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(String(url).includes('/api/status') ? status : {}), { status: 200 })))
  return withRouter(<NotificationsPage />)
}

describe('NotificationsPage', () => {
  it('lists the notices by day, with a count on each kind, and filters by kind', async () => {
    mount()
    expect(await screen.findByText('Telegram send failed for video 1')).toBeInTheDocument()
    expect(screen.getByText('Backup verified')).toBeInTheDocument()
    expect(within(screen.getByRole('tab', { name: /^All/ })).getByText('2')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /^Backups/ }))
    expect(screen.queryByText('Telegram send failed for video 1')).toBeNull()
    expect(screen.getByText('Backup verified')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /^Needs attention/ }))
    expect(screen.getByText('Telegram send failed for video 1')).toBeInTheDocument()
    expect(screen.queryByText('Backup verified')).toBeNull()
  })

  it('dismisses one notice, and clears all of them', async () => {
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Dismiss: Backup verified' }))
    await waitFor(() => expect(screen.queryByText('Backup verified')).toBeNull())
    expect(screen.getByText('Telegram send failed for video 1')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Clear all' }))
    expect(await screen.findByText('You’re all caught up')).toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem('ax_cleared_notices') || '[]').length).toBe(2)   // remembered for the next visit
    expect(screen.getByRole('button', { name: 'Clear all' })).toBeDisabled()
  })
})

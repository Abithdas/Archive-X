import { fireEvent, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import type { UpdateState } from '@/features/watch/nativeBridge'
import { OverviewPanel } from './panels'

const base: UpdateState = { phase: 'uptodate', current: '30.4.3', version: null, notes: null, received: 0, total: 0, percent: null, error: null, checkedAt: Math.floor(Date.now() / 1000) - 30 }
afterEach(() => { vi.unstubAllGlobals(); delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ })

function desktop(initial: UpdateState, after: Partial<Record<string, UpdateState>> = {}) {
  const calls: string[] = []
  ;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {
    invoke: vi.fn(async (cmd: string) => { calls.push(cmd); return after[cmd] ?? initial }),
  }
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: true, jobs: [] }), { status: 200 })))
  withRouter(<OverviewPanel go={() => undefined} />)
  return calls
}

describe('Overview, app update', () => {
  it('is not there in a browser, where the desktop app does the updating', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"ok":true,"jobs":[]}', { status: 200 })))
    withRouter(<OverviewPanel go={() => undefined} />)
    await screen.findByRole('region', { name: 'Uploads and downloads' })
    expect(screen.queryByRole('region', { name: 'App update' })).toBeNull()
  })
  it('says it is up to date, and looks again on request', async () => {
    const calls = desktop(base, { update_check: { ...base, phase: 'available', version: '30.5.0' } })
    const card = await screen.findByRole('region', { name: 'App update' })
    expect(await within(card).findByText('Archive X is up to date')).toBeInTheDocument()
    fireEvent.click(within(card).getByRole('button', { name: 'Check now' }))
    expect(await within(card).findByText('Version 30.5.0 is ready')).toBeInTheDocument()
    expect(calls).toContain('update_check')
  })
  it('starts the download from the button and shows the progress', async () => {
    const calls = desktop({ ...base, phase: 'available', version: '30.5.0' }, { update_install: { ...base, phase: 'downloading', version: '30.5.0', received: 5_000_000, total: 20_000_000, percent: 25 } })
    const card = await screen.findByRole('region', { name: 'App update' })
    fireEvent.click(await within(card).findByRole('button', { name: 'Update now' }))
    const bar = await within(card).findByRole('progressbar', { name: 'Update download' })
    expect(bar).toHaveAttribute('aria-valuenow', '25')
    expect(within(card).getByText('4.77 MB of 19.1 MB · 25%')).toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: 'Update now' })).toBeNull()      // nothing to press while it downloads
    expect(calls).toContain('update_install')
  })
  it('shows why it failed', async () => {
    desktop({ ...base, phase: 'error', error: 'the update could not be installed: bad signature', version: '30.5.0' })
    const card = await screen.findByRole('region', { name: 'App update' })
    expect(await within(card).findByText(/bad signature/)).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'Try the update again' })).toBeInTheDocument()
  })
})

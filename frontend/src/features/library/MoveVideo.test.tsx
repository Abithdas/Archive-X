import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { LibraryItem } from '@/api/types'
import { toast } from 'sonner'
import { CardMenu } from './CardMenu'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(() => 'id'), { error: vi.fn(), success: vi.fn(), loading: vi.fn(() => 'p'), dismiss: vi.fn() }), Toaster: () => null }))
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
  HTMLElement.prototype.hidePopover = function () {}
})
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks() })

const item = { id: 77, _cid: '-1001', media_id: 5, name: 'Scene', filename: 'f.mp4', size: 2048, duration: 60, width: 1, height: 1, resolution_category: '1080p', thumbnail_id: 77, favorite: 0, upload_date: null, channel_title: 'Main', performers: [], studios: [], tags: [], studio: '' } as LibraryItem
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const INVENTORY = [{ id: 1, telegram_id: '-1001', title: 'Main' }, { id: 2, telegram_id: '-1002', title: 'Beta' }, { id: 3, telegram_id: '-1003', title: 'Gamma' }]
type Call = { url: string; method: string; body: Record<string, unknown> | undefined }
const PUBLISHED = [{ id: 1, telegram_id: '-1001', title: 'Main' }, { id: 4, telegram_id: '-1009', title: 'Published only' }]
const mount = (over: { inventory?: () => Response; move?: () => Response; published?: unknown[] } = {}) => {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined })
    if (url === '/api/source-channels') return (over.inventory ?? (() => json(INVENTORY)))()
    if (url === '/api/channels') return json(over.published ?? PUBLISHED)
    if (url === '/api/library/move-delete') return (over.move ?? (() => json({ ok: true, moved: { source_deleted: true } })))()
    return json({ ok: true })
  }))
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><CardMenu item={item} /></QueryClientProvider>)
  return calls
}
const moves = (c: Call[]) => c.filter((x) => x.url === '/api/library/move-delete')
const open = () => fireEvent.click(screen.getByRole('menuitem', { name: 'Move / delete source', hidden: true }))
const picker = async () => { const dlg = await screen.findByRole('dialog', { name: /Move video$/ }); await waitFor(() => expect(within(dlg).getByRole('combobox')).toBeEnabled()); return dlg }

describe('Move / delete source', () => {
  it('lists Telegram channels except the source, with the video and its size', async () => {
    const calls = mount()
    expect(calls).toHaveLength(0)
    open()
    const dlg = await picker()
    const opts = within(dlg).getAllByRole('option').map((o) => o.textContent)
    expect(opts).toEqual(['Choose channel…', 'Beta', 'Gamma'])
    expect(dlg).toHaveTextContent('Main · 2 KB')
    expect(dlg).toHaveTextContent('3 channels loaded.')
    expect(moves(calls)).toHaveLength(0)
  })

  it('asks for a destination first', async () => {
    const calls = mount()
    open()
    const dlg = await picker()
    fireEvent.click(within(dlg).getByRole('button', { name: 'Move and delete source' }))
    expect(toast).toHaveBeenCalledWith('Choose a destination channel first.')
    expect(moves(calls)).toHaveLength(0)
  })

  it('needs a second, explicit confirmation before anything is sent', async () => {
    const calls = mount()
    open()
    const dlg = await picker()
    fireEvent.change(within(dlg).getByRole('combobox'), { target: { value: '-1002' } })
    fireEvent.click(within(dlg).getByRole('button', { name: 'Move and delete source' }))
    const ask = await screen.findByRole('dialog', { name: 'Move video and delete source' })
    expect(ask).toHaveTextContent('Move “Scene” to “Beta” and delete the original Telegram message after verification? This cannot be undone.')
    expect(moves(calls)).toHaveLength(0)
    fireEvent.click(within(ask).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Move video and delete source' })).toBeNull())
    expect(moves(calls)).toHaveLength(0)
    expect(screen.getByRole('dialog', { name: /Move video$/ })).toBeInTheDocument()
  })

  it('sends the explicit delete flag only after confirming, then reports the verified move', async () => {
    const calls = mount()
    open()
    const dlg = await picker()
    fireEvent.change(within(dlg).getByRole('combobox'), { target: { value: '-1003' } })
    fireEvent.click(within(dlg).getByRole('button', { name: 'Move and delete source' }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Move video and delete source' })).getByRole('button', { name: 'Move and delete source' }))
    await waitFor(() => expect(moves(calls)).toHaveLength(1))
    expect(moves(calls)[0]!.body).toEqual({ channel_id: -1001, message_id: 77, destination_channel: '-1003', confirm_delete: true })
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Move verified. The source message was deleted.', expect.anything()))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /Move video$/ })).toBeNull())
  })

  it('says when the source deletion is pending', async () => {
    mount({ move: () => json({ ok: true, moved: { source_deleted: false }, source_warning: 'Telegram is slow.' }) })
    open()
    const dlg = await picker()
    fireEvent.change(within(dlg).getByRole('combobox'), { target: { value: '-1002' } })
    fireEvent.click(within(dlg).getByRole('button', { name: 'Move and delete source' }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Move video and delete source' })).getByRole('button', { name: 'Move and delete source' }))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Destination verified. Source deletion is pending and will be retried. Telegram is slow.', expect.anything()))
  })

  it('shows the server reason and keeps the dialog open when the move fails', async () => {
    mount({ move: () => json({ detail: 'Verification failed; original kept.' }, 502) })
    open()
    const dlg = await picker()
    fireEvent.change(within(dlg).getByRole('combobox'), { target: { value: '-1002' } })
    fireEvent.click(within(dlg).getByRole('button', { name: 'Move and delete source' }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Move video and delete source' })).getByRole('button', { name: 'Move and delete source' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Verification failed; original kept.', expect.anything()))
    expect(screen.getByRole('dialog', { name: /Move video$/ })).toBeInTheDocument()
  })

  it('falls back to published channels with a warning when the inventory is unavailable', async () => {
    mount({ inventory: () => json({ detail: 'Telegram offline' }, 503) })
    open()
    const dlg = await picker()
    expect(within(dlg).getAllByRole('option').map((o) => o.textContent)).toEqual(['Choose channel…', 'Published only'])
    expect(dlg).toHaveTextContent('Could not load Telegram channels: Telegram offline.')
  })

  it('offers a refresh and disables the move when no destination exists', async () => {
    const calls = mount({ inventory: () => json([INVENTORY[0]]), published: [PUBLISHED[0]] })
    open()
    const dlg = await screen.findByRole('dialog', { name: /Move video$/ })
    fireEvent.click(await within(dlg).findByRole('button', { name: 'Refresh Telegram channels' }))
    await waitFor(() => expect(calls.filter((c) => c.url === '/api/source-channels').length).toBeGreaterThan(1))
    expect(within(dlg).getByRole('button', { name: 'Move and delete source' })).toBeDisabled()
  })
})

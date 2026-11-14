import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { LibraryItem } from '@/api/types'
import { toast } from 'sonner'
import { CardMenu } from './CardMenu'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(() => 'id'), { error: vi.fn(), success: vi.fn(), loading: vi.fn(() => 'pending'), dismiss: vi.fn() }), Toaster: () => null }))
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
  HTMLElement.prototype.hidePopover = function () {}
})
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks() })

const item = { id: 77, _cid: '-1001', media_id: 5, name: 'Scene', filename: 'f.mp4', size: 1, duration: 60, width: 1, height: 1, resolution_category: '1080p', thumbnail_id: 77, favorite: 0, upload_date: null, channel_title: 'Main', performers: [], studios: [], tags: [], studio: '' } as LibraryItem
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const CHANNELS = [{ id: 1, telegram_id: '-100A', title: 'Alpha Videos' }, { id: 2, telegram_id: '-100B', title: 'Beta' }]
type Call = { url: string; method: string; body: Record<string, unknown> | undefined }
const mount = (cfg: Record<string, unknown>, sendReply: () => Response = () => json({ ok: true })) => {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined })
    if (url === '/api/settings/send') return json(cfg)
    if (url === '/api/channels') return json(CHANNELS)
    if (url === '/api/send/preview') return json({ caption: '<b>Line one</b>\nLine two &lt;script&gt;' })
    if (url === '/api/send') return sendReply()
    return json({ ok: true })
  }))
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><CardMenu item={item} /></QueryClientProvider>)
  return calls
}
const sends = (c: Call[]) => c.filter((x) => x.url === '/api/send')
const open = () => fireEvent.click(screen.getByRole('menuitem', { name: 'Send to Telegram', hidden: true }))

describe('Send to Telegram', () => {
  it('makes no request until the menu item is used', () => {
    const calls = mount({})
    expect(calls).toHaveLength(0)
  })

  it('sends straight away when "always send to this channel" is saved', async () => {
    const calls = mount({ target: '-100B', fixed: true })
    open()
    await waitFor(() => expect(sends(calls)).toHaveLength(1))
    expect(sends(calls)[0]!.body).toEqual({ channel_id: '-1001', message_id: 77, target: '-100B', remember: false, force: false })
    expect(screen.queryByRole('dialog')).toBeNull()
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Sent to Beta', expect.anything()))
  })

  it('asks for a channel, shows the caption, and sends nothing until Send is pressed', async () => {
    const calls = mount({ target: '', fixed: false })
    open()
    const dlg = await screen.findByRole('dialog')
    const cap = await within(dlg).findByLabelText('Caption preview')
    await waitFor(() => expect(cap.querySelector('b')).toHaveTextContent('Line one'))
    expect(cap).toHaveTextContent('Line one Line two <script>')
    expect(cap.querySelector('script')).toBeNull()
    expect(sends(calls)).toHaveLength(0)
    fireEvent.click(await within(dlg).findByRole('option', { name: /Alpha Videos/ }))
    fireEvent.click(within(dlg).getByRole('checkbox'))
    fireEvent.click(within(dlg).getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(sends(calls)).toHaveLength(1))
    expect(sends(calls)[0]!.body).toMatchObject({ target: '-100A', remember: true, force: false })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('preselects the saved target and filters channels by search', async () => {
    mount({ target: '-100B', fixed: false })
    open()
    const dlg = await screen.findByRole('dialog')
    expect(await within(dlg).findByRole('option', { name: /Beta/ })).toHaveAttribute('aria-selected', 'true')
    fireEvent.change(within(dlg).getByLabelText('Search channel'), { target: { value: 'alpha' } })
    expect(within(dlg).queryByRole('option', { name: /Beta/ })).toBeNull()
    expect(within(dlg).getByRole('option', { name: /Alpha Videos/ })).toBeInTheDocument()
  })

  it('does not send without a chosen channel', async () => {
    const calls = mount({ target: '', fixed: false })
    open()
    const dlg = await screen.findByRole('dialog')
    fireEvent.click(within(dlg).getByRole('button', { name: 'Send' }))
    expect(toast).toHaveBeenCalledWith('Choose a channel first.')
    expect(sends(calls)).toHaveLength(0)
  })

  it('asks before sending a duplicate, and re-sends with force only after confirmation', async () => {
    let n = 0
    const calls = mount({ target: '-100A', fixed: true }, () => (++n === 1 ? json({ already_sent: true, detail: 'This video was already sent to that channel' }, 409) : json({ ok: true })))
    open()
    const dlg = await screen.findByRole('dialog')
    expect(dlg).toHaveTextContent('already sent to Alpha Videos')
    expect(sends(calls)).toHaveLength(1)
    fireEvent.click(within(dlg).getByRole('button', { name: 'Send again' }))
    await waitFor(() => expect(sends(calls)).toHaveLength(2))
    expect(sends(calls)[1]!.body).toMatchObject({ target: '-100A', force: true })
  })

  it('cancelling the duplicate prompt sends nothing more', async () => {
    const calls = mount({ target: '-100A', fixed: true }, () => json({ already_sent: true, detail: 'dup' }, 409))
    open()
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Not sent.'))
    expect(sends(calls)).toHaveLength(1)
  })

  it('shows Telegram\'s reason when the send fails', async () => {
    mount({ target: '-100A', fixed: true }, () => json({ detail: 'Telegram refused: FloodWait' }, 502))
    open()
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Send failed: Telegram refused: FloodWait', expect.anything()))
  })
})

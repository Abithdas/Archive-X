import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LibraryItem } from '@/api/types'
import { toast } from 'sonner'
import { SelectionBar } from './SelectionBar'
import { useSelection } from './selection'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(() => 'id'), { error: vi.fn(), success: vi.fn(), loading: vi.fn(() => 'p'), dismiss: vi.fn() }), Toaster: () => null }))
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
  HTMLElement.prototype.hidePopover = function () {}
})
beforeEach(() => useSelection.getState().exit())
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks() })

const mk = (id: number) => ({ id, _cid: '-1', media_id: id, name: `V${id}`, thumbnail_id: id, performers: [], studios: [], tags: [], studio: '' }) as unknown as LibraryItem
const VISIBLE = [mk(1), mk(2), mk(3)]
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
type Call = { url: string; method: string; body: Record<string, unknown> | undefined }
const CHANNELS = [{ id: 1, telegram_id: '-100A', title: 'Alpha' }, { id: 2, telegram_id: '-100B', title: 'Beta' }]
const mount = (reply: (url: string, body: Record<string, unknown> | undefined, n: number) => Response | undefined = () => undefined, picks = 2) => {
  const calls: Call[] = []
  const counts: Record<string, number> = {}
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined
    calls.push({ url, method: init?.method ?? 'GET', body })
    counts[url] = (counts[url] ?? 0) + 1
    const x = reply(url, body, counts[url]!)
    if (x) return x
    if (url === '/api/channels') return json(CHANNELS)
    if (url === '/api/settings/send') return json({ target: '', fixed: false })
    if (url === '/api/send/preview') return json({ caption: '<b>Cap</b>' })
    if (url.startsWith('/api/send/bulk/status/')) return json({ active: false })
    return json({ ok: true })
  }))
  act(() => { useSelection.getState().enter(); for (let i = 1; i <= picks; i++) useSelection.getState().toggle('-1', i) })
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><SelectionBar visible={VISIBLE} /></QueryClientProvider>)
  return calls
}
const menuItem = (menu: string, name: string) => {
  const m = screen.getByRole('menu', { name, hidden: true })
  return within(m).getByRole('menuitem', { name: menu, hidden: true })
}
const posts = (c: Call[], url: string) => c.filter((x) => x.url === url && x.method === 'POST')

describe('selection bar', () => {
  it('shows the count and selects all visible, deselects, and finishes', () => {
    mount()
    expect(screen.getByRole('status')).toHaveTextContent('2 selected')
    fireEvent.click(screen.getByRole('button', { name: 'Select all' }))
    expect(screen.getByRole('status')).toHaveTextContent('3 selected')
    fireEvent.click(screen.getByRole('button', { name: 'Deselect' }))
    expect(screen.getByRole('status')).toHaveTextContent('0 selected')
    expect(useSelection.getState().active).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(useSelection.getState().active).toBe(false)
  })

  it('asks for a selection before doing anything', () => {
    const calls = mount(undefined, 0)
    fireEvent.click(menuItem('Recheck Metadata', 'Metadata'))
    expect(toast).toHaveBeenCalledWith('Select at least one video.')
    expect(calls).toHaveLength(0)
  })

  it('queues a metadata recheck for the picked videos', async () => {
    const calls = mount((u) => (u === '/api/mapping/recheck' ? json({ ok: true, count: 2 }) : undefined))
    fireEvent.click(menuItem('Recheck Metadata', 'Metadata'))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Metadata recheck queued for 2 video(s).'))
    expect(posts(calls, '/api/mapping/recheck')[0]!.body).toEqual({ videos: [{ channel_id: '-1', message_id: 1 }, { channel_id: '-1', message_id: 2 }] })
  })

  it('edits the title for all picked videos, locked, and clears the selection', async () => {
    const calls = mount((u) => (u === '/api/mapping/bulk' ? json({ ok: true, changed: 2 }) : undefined))
    fireEvent.click(menuItem('Edit Title', 'Metadata'))
    const dlg = await screen.findByRole('dialog', { name: 'Correct video title' })
    fireEvent.click(within(dlg).getByRole('button', { name: 'Apply value' }))
    expect(await within(dlg).findByRole('alert')).toHaveTextContent('Enter the corrected video title.')
    expect(posts(calls, '/api/mapping/bulk')).toHaveLength(0)
    fireEvent.change(within(dlg).getByLabelText('Video title'), { target: { value: ' Fixed ' } })
    fireEvent.click(within(dlg).getByRole('button', { name: 'Apply value' }))
    await waitFor(() => expect(posts(calls, '/api/mapping/bulk')).toHaveLength(1))
    expect(posts(calls, '/api/mapping/bulk')[0]!.body).toMatchObject({ field: 'title', target_name: 'Fixed', action: 'move', lock: true })
    await waitFor(() => expect(toast).toHaveBeenCalledWith('2 video(s) updated and locked.'))
    expect(useSelection.getState().picked).toEqual({})
  })

  it('asks before setting performer/studio to Unknown, and sends nothing on cancel', async () => {
    const calls = mount()
    fireEvent.click(menuItem('Clear Performer', 'Performer / Studio'))
    const dlg = await screen.findByRole('dialog', { name: 'Set to Unknown?' })
    expect(dlg).toHaveTextContent('performer of 2 videos to Unknown')
    fireEvent.click(within(dlg).getByRole('button', { name: 'Cancel' }))
    expect(posts(calls, '/api/mapping/bulk')).toHaveLength(0)
    fireEvent.click(menuItem('Clear Studio', 'Performer / Studio'))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Set to Unknown?' })).getByRole('button', { name: 'Clear studio' }))
    await waitFor(() => expect(posts(calls, '/api/mapping/bulk')).toHaveLength(1))
    expect(posts(calls, '/api/mapping/bulk')[0]!.body).toMatchObject({ field: 'site', action: 'unknown', target_name: '', lock: true })
  })

  it('moves everything to Unknown only after confirming', async () => {
    const calls = mount()
    fireEvent.click(menuItem('Move All to Unknown', 'More'))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Set to Unknown?' })).getByRole('button', { name: 'Move all to Unknown' }))
    await waitFor(() => expect(posts(calls, '/api/mapping/bulk')[0]?.body).toMatchObject({ field: 'all', action: 'unknown' }))
  })

  it('removes mapping locks', async () => {
    const calls = mount()
    fireEvent.click(menuItem('Unlock', 'More'))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Mapping locks removed.'))
    expect(posts(calls, '/api/mapping/unlock')[0]!.body).toMatchObject({ field: 'all' })
  })

  it('shows the server reason when a bulk change fails', async () => {
    mount((u) => (u === '/api/mapping/bulk' ? json({ detail: 'Invalid mapping field' }, 400) : undefined))
    fireEvent.click(menuItem('Edit Title', 'Metadata'))
    const dlg = await screen.findByRole('dialog', { name: 'Correct video title' })
    fireEvent.change(within(dlg).getByLabelText('Video title'), { target: { value: 'X' } })
    fireEvent.click(within(dlg).getByRole('button', { name: 'Apply value' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Invalid mapping field'))
  })
})

describe('bulk send to Telegram', () => {
  it('sends the picked videos to the chosen channel and reports the summary', async () => {
    const calls = mount((u) => (u === '/api/send/bulk' ? json({ ok: true, sent: 2, skipped: 0, failed: 0 }) : undefined))
    fireEvent.click(screen.getByRole('button', { name: /Send to Telegram/ }))
    const dlg = await screen.findByRole('dialog', { name: 'Send 2 selected videos to Telegram' })
    expect(dlg).toHaveTextContent('Caption preview for the first selected video.')
    fireEvent.click(await within(dlg).findByRole('option', { name: /Beta/ }))
    fireEvent.click(within(dlg).getByRole('button', { name: 'Send 2 videos' }))
    await waitFor(() => expect(posts(calls, '/api/send/bulk')).toHaveLength(1))
    const body = posts(calls, '/api/send/bulk')[0]!.body!
    expect(body).toMatchObject({ target: '-100B', remember: false, videos: [{ channel_id: '-1', message_id: 1 }, { channel_id: '-1', message_id: 2 }] })
    expect(String(body.job_id)).toMatch(/^bulk-/)
    expect(body.duplicate_action).toBeUndefined()
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Telegram send complete: 2 sent, 0 skipped, 0 failed.', expect.anything()))
  })

  it('sends at once when "always send to this channel" is saved', async () => {
    const calls = mount((u) => (u === '/api/settings/send' ? json({ target: '-100A', fixed: true }) : u === '/api/send/bulk' ? json({ ok: true, sent: 2 }) : undefined))
    fireEvent.click(screen.getByRole('button', { name: /Send to Telegram/ }))
    await waitFor(() => expect(posts(calls, '/api/send/bulk')).toHaveLength(1))
    expect(posts(calls, '/api/send/bulk')[0]!.body).toMatchObject({ target: '-100A' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('asks about duplicates, and "Skip duplicates" re-sends with duplicate_action=skip', async () => {
    const calls = mount((u, _b, n) => (u === '/api/send/bulk' ? (n === 1 ? json({ ok: true, already_sent: true, duplicate_videos: [{}] }) : json({ ok: true, sent: 1, skipped: 1, failed: 0 })) : u === '/api/settings/send' ? json({ target: '-100A', fixed: true }) : undefined))
    fireEvent.click(screen.getByRole('button', { name: /Send to Telegram/ }))
    const dlg = await screen.findByRole('dialog', { name: 'Some videos were already sent' })
    expect(dlg).toHaveTextContent('1 of the selected videos has already been sent to Alpha')
    expect(posts(calls, '/api/send/bulk')).toHaveLength(1)
    fireEvent.click(within(dlg).getByRole('button', { name: 'Skip duplicates' }))
    await waitFor(() => expect(posts(calls, '/api/send/bulk')).toHaveLength(2))
    expect(posts(calls, '/api/send/bulk')[1]!.body).toMatchObject({ duplicate_action: 'skip' })
  })

  it('"Send again" re-sends with duplicate_action=resend; Cancel sends nothing more', async () => {
    const calls = mount((u, _b, n) => (u === '/api/send/bulk' ? (n === 1 ? json({ ok: true, already_sent: true, duplicate_videos: [{}, {}] }) : json({ ok: true, sent: 2 })) : u === '/api/settings/send' ? json({ target: '-100A', fixed: true }) : undefined))
    fireEvent.click(screen.getByRole('button', { name: /Send to Telegram/ }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Some videos were already sent' })).getByRole('button', { name: 'Send again' }))
    await waitFor(() => expect(posts(calls, '/api/send/bulk')[1]?.body).toMatchObject({ duplicate_action: 'resend' }))
  })

  it('cancelling the duplicate prompt sends nothing', async () => {
    const calls = mount((u) => (u === '/api/send/bulk' ? json({ ok: true, already_sent: true, duplicate_videos: [{}] }) : u === '/api/settings/send' ? json({ target: '-100A', fixed: true }) : undefined))
    fireEvent.click(screen.getByRole('button', { name: /Send to Telegram/ }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Some videos were already sent' })).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Not sent.'))
    expect(posts(calls, '/api/send/bulk')).toHaveLength(1)
  })

  it('reports the first failure and keeps the picker open on a request error', async () => {
    mount((u) => (u === '/api/send/bulk' ? json({ detail: 'Telegram is not connected' }, 503) : undefined))
    fireEvent.click(screen.getByRole('button', { name: /Send to Telegram/ }))
    const dlg = await screen.findByRole('dialog', { name: 'Send 2 selected videos to Telegram' })
    fireEvent.click(await within(dlg).findByRole('option', { name: /Alpha/ }))
    fireEvent.click(within(dlg).getByRole('button', { name: 'Send 2 videos' }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Bulk send failed: Telegram is not connected', expect.anything()))
    expect(await screen.findByRole('dialog', { name: 'Send 2 selected videos to Telegram' })).toBeInTheDocument()
  })
})

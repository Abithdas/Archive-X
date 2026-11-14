import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { ChannelDuplicateCard } from './ChannelDuplicates'

const toast = vi.hoisted(() => Object.assign(vi.fn(), { error: vi.fn() }))
vi.mock('sonner', () => ({ toast, Toaster: () => null }))
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(() => { vi.unstubAllGlobals(); toast.mockClear() })

const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const row = (mid: number, extra = {}) => ({ channel_id: '-1001', message_id: mid, title: `Clip ${mid}`, size: 2048, width: 1280, height: 720, duration: 61, document_id: 'D1', channel_title: 'Main', ...extra })
const SCAN = { ok: true, scope: 'all_indexed', indexed_video_count: 5, indexed_channel_count: 2, complete_channel_count: 1, groups: [{ duplicate_count: 3, proof: ['Same Telegram document ID'], candidates: [row(1), row(2), row(3)] }] }
type Call = { url: string; method: string; body?: Record<string, unknown> }
const mount = (over: (u: string, i?: RequestInit) => Response | undefined = () => undefined) => {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const o = over(url, init)
    if (o) return o
    if (url === '/api/source-channels') return json([{ telegram_id: '-1001', title: 'Main' }, { telegram_id: '-1002', title: 'Alpha' }])
    if (url.startsWith('/api/channel-maintenance/duplicates')) return json(SCAN)
    if (url.startsWith('/api/play/preview-manifest')) return json({ ready: false, frame_times: [] })
    if (url === '/api/channel-maintenance/delete-duplicate') return json({ ok: true })
    return json({})
  }))
  withRouter(<ChannelDuplicateCard />)
  return calls
}
const open = async (scope = '__all_indexed__') => {
  const sel = await screen.findByLabelText('Duplicate scan scope')
  await waitFor(() => expect(within(sel).getAllByRole('option').length).toBeGreaterThan(2))
  fireEvent.change(sel, { target: { value: scope } })
  fireEvent.click(screen.getByRole('button', { name: /Scan for duplicates/ }))
  return screen.findByRole('dialog')
}
const deletes = (c: Call[]) => c.filter((x) => x.url === '/api/channel-maintenance/delete-duplicate')

describe('Duplicate video review', () => {
  it('lists the saved channels after the cross-channel choice, and asks to choose first', async () => {
    mount()
    const sel = await screen.findByLabelText('Duplicate scan scope')
    await waitFor(() => expect(within(sel).getAllByRole('option').map((o) => o.textContent)).toEqual(['Choose a scan scope…', 'All indexed videos · local review', 'Alpha', 'Main']))
    fireEvent.click(screen.getByRole('button', { name: /Scan for duplicates/ }))
    expect(toast).toHaveBeenCalledWith('Choose a channel or All indexed videos to scan first.')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('scans read-only and shows groups with each copy\'s facts', async () => {
    const calls = mount()
    const dlg = await open()
    expect(await within(dlg).findByText('Group 1')).toBeInTheDocument()
    expect(calls.filter((c) => c.url.startsWith('/api/channel-maintenance/duplicates')).map((c) => [c.url, c.method])).toEqual([['/api/channel-maintenance/duplicates/all', 'GET']])
    expect(within(dlg).getByText('3 matching copies', { exact: false })).toBeInTheDocument()
    expect(within(dlg).getAllByText('2.0 KB').length).toBe(3)
    expect(deletes(calls)).toHaveLength(0)
  })

  it('will not delete until a matching copy is left unselected, and sends nothing on cancel', async () => {
    const calls = mount()
    const dlg = await open()
    await within(dlg).findByText('Group 1')
    const box = (n: number) => within(dlg).getByRole('checkbox', { name: new RegExp(`Clip ${n} from Main, message ${n}`) })
    const del = () => within(dlg).getByRole('button', { name: /^Delete/ })
    expect(del()).toBeDisabled()
    ;[1, 2, 3].forEach((n) => fireEvent.click(box(n)))
    expect(del()).toBeDisabled()
    expect(within(dlg).getByText(/Leave one directly matching copy/)).toBeInTheDocument()
    fireEvent.click(box(3))
    expect(del()).toBeEnabled()
    fireEvent.click(del())
    const ask = await screen.findByRole('dialog', { name: 'Delete 2 selected videos?' })
    expect(ask).toHaveTextContent('Clip 1 — Main, message 1, 1280×720, 2.0 KB')
    fireEvent.click(within(ask).getByRole('button', { name: 'Cancel' }))
    expect(deletes(calls)).toHaveLength(0)
  })

  it('deletes one at a time against the kept copy, then scans again', async () => {
    const calls = mount()
    const dlg = await open()
    await within(dlg).findByText('Group 1')
    fireEvent.click(within(dlg).getByRole('checkbox', { name: /Clip 1 from/ }))
    fireEvent.click(within(dlg).getByRole('checkbox', { name: /Clip 2 from/ }))
    fireEvent.click(within(dlg).getByRole('button', { name: /^Delete/ }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Delete 2 selected videos?' })).getByRole('button', { name: 'Delete 2 videos' }))
    await waitFor(() => expect(deletes(calls)).toHaveLength(2))
    expect(deletes(calls).map((d) => d.body)).toEqual([
      { confirm_delete: true, channel_id: -1001, message_id: 1, keep_channel_id: -1001, keep_message_id: 3 },
      { confirm_delete: true, channel_id: -1001, message_id: 2, keep_channel_id: -1001, keep_message_id: 3 },
    ])
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Deleted 2 selected videos across 1 channel.', { duration: 10000 }))
    expect(calls.filter((c) => c.url === '/api/channel-maintenance/duplicates/all').length).toBe(2)
  })

  it('stops the batch at the first failure and says how far it got', async () => {
    let n = 0
    const calls = mount((u) => (u === '/api/channel-maintenance/delete-duplicate' ? (++n === 1 ? json({ ok: true }) : json({ detail: 'Telegram refused' }, 400)) : undefined))
    const dlg = await open()
    await within(dlg).findByText('Group 1')
    fireEvent.click(within(dlg).getByRole('checkbox', { name: /Clip 1 from/ }))
    fireEvent.click(within(dlg).getByRole('checkbox', { name: /Clip 2 from/ }))
    fireEvent.click(within(dlg).getByRole('button', { name: /^Delete/ }))
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Delete 2 selected videos?' })).getByRole('button', { name: 'Delete 2 videos' }))
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Deleted 1 of 2; stopped before Main · message 2. Telegram refused', { duration: 10000 }))
    expect(deletes(calls)).toHaveLength(2)
    expect(await screen.findByText('Batch stopped after 1 of 2 deletions. Telegram refused')).toBeInTheDocument()
  })

  it('shows the server\'s reason when a channel scan fails, and an empty result plainly', async () => {
    mount((u) => (u.startsWith('/api/channel-maintenance/duplicates?') ? json({ detail: 'Connect Telegram before scanning a channel' }, 400) : undefined))
    const dlg = await open('-1001')
    expect(await within(dlg).findByText('Connect Telegram before scanning a channel')).toBeInTheDocument()
  })

  it('says when there are no duplicates', async () => {
    mount((u) => (u === '/api/channel-maintenance/duplicates/all' ? json({ ...SCAN, groups: [] }) : undefined))
    const dlg = await open()
    expect(await within(dlg).findByText('No verified duplicate videos found.')).toBeInTheDocument()
    expect(within(dlg).getByText(/No Telegram messages were changed/)).toBeInTheDocument()
  })
})

import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { TransferWorkflow } from './TransferWorkflow'

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(() => vi.unstubAllGlobals())

const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const channels = [{ telegram_id: '-1001', title: 'Main Archive', video_count: 18 }, { telegram_id: '-1002', title: 'Misc', video_count: 4 }]
type Handler = (url: string, init?: RequestInit) => Response | undefined
const mount = (extra: Handler = () => undefined) => {
  const calls: Array<{ url: string; body: Record<string, unknown> | undefined }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined })
    const x = extra(url, init)
    if (x) return x
    if (url === '/api/source-channels') return json(channels)
    if (url.startsWith('/api/settings/folder-preference')) return json({ ok: true, path: '' })
    return json({})
  }))
  withRouter(<TransferWorkflow />)
  return calls
}
const next = (re: RegExp) => fireEvent.click(screen.getByRole('button', { name: re }))
const fillLocal = async (folder = 'D:\\clips') => {
  fireEvent.change(await screen.findByLabelText('Source folder path'), { target: { value: folder } })
  fireEvent.change(await screen.findByRole('combobox', { name: 'Destination channel' }), { target: { value: '-1001' } })
  await screen.findByRole('option', { name: 'Main Archive' })
}
const toRun = () => { next(/Next: Options/); next(/Next: Review/); next(/Next: Run/) }

describe('Local files', () => {
  it('blocks Next until a folder and a destination are chosen', async () => {
    mount()
    const btn = await screen.findByRole('button', { name: /Next: Options/ })
    expect(btn).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Source folder path'), { target: { value: 'D:\\clips' } })
    expect(btn).toBeDisabled()
    await screen.findByRole('option', { name: 'Main Archive' })
    fireEvent.change(screen.getByRole('combobox', { name: 'Destination channel' }), { target: { value: '-1001' } })
    expect(btn).toBeEnabled()
  })
  it('starts a video transfer with the filters, and reports success', async () => {
    const calls = mount((url) => (url === '/api/telegram-transfer/start' ? json({ id: 'job1' }) : undefined))
    await fillLocal()
    next(/Next: Options/)
    fireEvent.change(screen.getByLabelText('Source resolution for local videos'), { target: { value: '1080P' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /Include subfolders/ }))
    next(/Next: Review/)
    expect(screen.getByText('D:\\clips')).toBeInTheDocument()
    next(/Next: Run/)
    fireEvent.click(screen.getByRole('button', { name: 'Start local transfer' }))
    expect(await screen.findByText('Local preparation/transfer queued.')).toBeInTheDocument()
    expect(calls.find((c) => c.url === '/api/telegram-transfer/start')!.body).toEqual({
      source: 'local', media_type: 'VIDEO', folder: 'D:\\clips', destination_channel: '-1001', include_subfolders: true, delete_local_after_delivery: true, resolution_filter: '1080P', orientation: 'BOTH', oversized_file_policy: 'optimize',
    })
  })
  it('shows the backend reason when Telegram refuses to start', async () => {
    mount((url) => (url === '/api/telegram-transfer/start' ? json({ detail: 'Telegram is offline' }) : undefined))
    await fillLocal()
    toRun()
    fireEvent.click(screen.getByRole('button', { name: 'Start local transfer' }))
    expect(await screen.findByText('Could not start: Telegram is offline')).toBeInTheDocument()
  })
  it('pre-fills the saved folder and can browse and save a folder', async () => {
    const calls = mount((url) => (url.startsWith('/api/settings/folder-preference?') ? json({ ok: true, path: 'E:\\saved' }) : url === '/api/settings/folder-picker' ? json({ path: 'F:\\picked' }) : url === '/api/settings/folder-preference' ? json({ ok: true, path: 'F:\\picked' }) : undefined))
    await waitFor(() => expect(screen.getByLabelText('Source folder path')).toHaveValue('E:\\saved'))
    fireEvent.click(screen.getByRole('button', { name: 'Browse…' }))
    await waitFor(() => expect(screen.getByLabelText('Source folder path')).toHaveValue('F:\\picked'))
    expect(screen.getByText(/Click Save folder to remember it/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Save folder' }))
    expect(await screen.findByText('Folder saved.')).toBeInTheDocument()
    expect(calls.find((c) => c.url === '/api/settings/folder-preference' && c.body)!.body).toEqual({ key: 'transfer_source', path: 'F:\\picked' })
  })
})

describe('Image upload', () => {
  const images = async () => { await fillLocal('D:\\photos'); fireEvent.click(screen.getByRole('checkbox', { name: /Upload images/ })) }
  it('scans first, then queues PHOTOS without video filters', async () => {
    const calls = mount((url) => (url === '/api/telegram-transfer/scan-photos' ? json({ direct_count: 4, nested_count: 0, recursive_count: 4 }) : url === '/api/telegram-transfer/start' ? json({ id: 'j' }) : undefined))
    await images()
    toRun()
    fireEvent.click(screen.getByRole('button', { name: 'Start image upload' }))
    expect(await screen.findByText(/Image document upload queued \(4 supported photo\(s\) detected\)/)).toBeInTheDocument()
    const body = calls.find((c) => c.url === '/api/telegram-transfer/start')!.body!
    expect(body.media_type).toBe('PHOTOS')
    expect('resolution_filter' in body).toBe(false)
  })
  it('asks about subfolders and includes them when you say yes', async () => {
    const calls = mount((url) => (url === '/api/telegram-transfer/scan-photos' ? json({ direct_count: 2, nested_count: 5, recursive_count: 7 }) : url === '/api/telegram-transfer/start' ? json({ id: 'j' }) : undefined))
    await images()
    toRun()
    fireEvent.click(screen.getByRole('button', { name: 'Start image upload' }))
    expect(await screen.findByText(/2 supported photo\(s\) directly in the selected folder and 5 more in subfolders/)).toBeInTheDocument()
    expect(calls.some((c) => c.url === '/api/telegram-transfer/start')).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Include subfolders' }))
    expect(await screen.findByText(/7 supported photo\(s\) detected/)).toBeInTheDocument()
    expect(calls.find((c) => c.url === '/api/telegram-transfer/start')!.body!.include_subfolders).toBe(true)
  })
  it('queues only the direct photos when you decline, and nothing when none are direct', async () => {
    const calls = mount((url) => (url === '/api/telegram-transfer/scan-photos' ? json({ direct_count: 0, nested_count: 3, recursive_count: 3 }) : undefined))
    await images()
    toRun()
    fireEvent.click(screen.getByRole('button', { name: 'Start image upload' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    expect(await screen.findByText(/No upload queued\. 3 supported photo\(s\) are in subfolders/)).toBeInTheDocument()
    expect(calls.some((c) => c.url === '/api/telegram-transfer/start')).toBe(false)
  })
  it('does not queue anything when the folder scan fails', async () => {
    const calls = mount((url) => (url === '/api/telegram-transfer/scan-photos' ? json({ detail: 'Folder not found' }, 400) : undefined))
    await images()
    toRun()
    fireEvent.click(screen.getByRole('button', { name: 'Start image upload' }))
    expect(await screen.findByText(/Could not scan the selected photo folder: Folder not found/)).toBeInTheDocument()
    expect(calls.some((c) => c.url === '/api/telegram-transfer/start')).toBe(false)
  })
})

describe('Telegram media', () => {
  const toTelegram = async () => {
    fireEvent.click(await screen.findByRole('button', { name: 'Telegram media' }))
    fireEvent.click(await screen.findByRole('checkbox', { name: /Main Archive/ }))
  }
  it('refuses a destination that is also a source, with the classic wording', async () => {
    mount()
    await toTelegram()
    await screen.findByRole('option', { name: 'Main Archive' })
    fireEvent.change(screen.getByRole('combobox', { name: 'Destination channel' }), { target: { value: '-1001' } })
    expect(screen.getByRole('button', { name: /Next: Options/ })).toBeDisabled()
    fireEvent.change(screen.getByRole('combobox', { name: 'Destination channel' }), { target: { value: '-1002' } })
    expect(screen.getByRole('button', { name: /Next: Options/ })).toBeEnabled()
  })
  it('requires a trash channel when cleanup is on, and sends nested checks', async () => {
    const calls = mount((url) => (url === '/api/telegram-transfer/start' ? json({ id: 'tj' }) : undefined))
    await toTelegram()
    await screen.findByRole('option', { name: 'Misc' })
    fireEvent.change(screen.getByRole('combobox', { name: 'Destination channel' }), { target: { value: '-1002' } })
    next(/Next: Options/)
    fireEvent.click(screen.getByRole('checkbox', { name: /Cleanup broken/ }))
    expect(screen.getByRole('alert')).toHaveTextContent('Choose a trash channel when cleanup is enabled.')
    expect(screen.getByRole('button', { name: /Next: Review/ })).toBeDisabled()
    fireEvent.change(screen.getByRole('combobox', { name: 'Trash channel' }), { target: { value: '-1001' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /Check Archive-X DB/ }))
    next(/Next: Review/)
    next(/Next: Run/)
    fireEvent.click(screen.getByRole('button', { name: 'Start Telegram transfer' }))
    expect(await screen.findByText('Telegram media inspection/preparation queued.')).toBeInTheDocument()
    expect(calls.find((c) => c.url === '/api/telegram-transfer/start')!.body).toMatchObject({ source: 'telegram', source_channels: ['-1001'], destination_channel: '-1002', duplicate_check: { source: true, destination: true, database: false }, cleanup: { enabled: true, trash_channel: '-1001' } })
  })
})

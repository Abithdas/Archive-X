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
  it('skips videos without saved snapshots by default, and can be told to make them first', async () => {
    const calls = mount((url) => (url === '/api/telegram-transfer/start' ? json({ id: 'tj' }) : undefined))
    await toTelegram()
    await screen.findByRole('option', { name: 'Misc' })
    fireEvent.change(screen.getByRole('combobox', { name: 'Destination channel' }), { target: { value: '-1002' } })
    next(/Next: Options/)
    const choice = screen.getByRole('combobox', { name: 'Videos without saved snapshots' }) as HTMLSelectElement
    expect(choice.value).toBe('existing')
    fireEvent.change(choice, { target: { value: 'generate' } })
    next(/Next: Review/)
    expect(screen.getByText('Make them from Telegram first')).toBeInTheDocument()
    next(/Next: Run/)
    fireEvent.click(screen.getByRole('button', { name: 'Start Telegram transfer' }))
    await screen.findByText('Telegram media inspection/preparation queued.')
    expect(calls.find((c) => c.url === '/api/telegram-transfer/start')!.body).toMatchObject({ snapshot_policy: 'generate' })
  })
  it('sends a length limit typed in minutes, and blocks one that is not a number', async () => {
    const calls = mount((url) => (url === '/api/telegram-transfer/start' ? json({ id: 'tj' }) : undefined))
    await toTelegram()
    await screen.findByRole('option', { name: 'Misc' })
    fireEvent.change(screen.getByRole('combobox', { name: 'Destination channel' }), { target: { value: '-1002' } })
    next(/Next: Options/)
    const box = screen.getByRole('textbox', { name: 'Only Telegram videos shorter than this many minutes' })
    fireEvent.change(box, { target: { value: 'ten' } })
    expect(screen.getByRole('alert')).toHaveTextContent('Enter the maximum length as minutes above 0')
    expect(screen.getByRole('button', { name: /Next: Review/ })).toBeDisabled()
    fireEvent.change(box, { target: { value: '10' } })
    next(/Next: Review/)
    expect(screen.getByText('Shorter than 10 min')).toBeInTheDocument()
    next(/Next: Run/)
    fireEvent.click(screen.getByRole('button', { name: 'Start Telegram transfer' }))
    await screen.findByText('Telegram media inspection/preparation queued.')
    expect(calls.find((c) => c.url === '/api/telegram-transfer/start')!.body).toMatchObject({ max_duration_minutes: 10 })
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

describe('A scanned local folder', () => {
  const folders = [{ id: 3, path: 'D:\\Films', title: 'Films', content_rating: 'sfw', videos: 12, exists: true }, { id: 4, path: 'D:\\Other', title: null, content_rating: 'nsfw', videos: 2, exists: true }]
  const sourceChannels = [
    { telegram_id: '-1001', title: 'Family films', content_rating: 'sfw', role: 'available' },
    { telegram_id: '-1002', title: 'Adult archive', content_rating: 'nsfw', role: 'available' },
    { telegram_id: '-9000000000003', title: 'Films', content_rating: 'sfw', role: 'local' },
  ]
  const withFolders: Handler = (url) => (url === '/api/local-folders' ? json({ folders, mode: 'sfw' }) : url === '/api/source-channels' ? json(sourceChannels) : url.startsWith('/api/local-folders/3/upload') ? json({ seeded: 10, rebuilt: 1, skipped: 1, destination: 'Family films' }) : undefined)

  it('is offered as a source and uploads by its own route, so snapshots and names are reused', async () => {
    const calls = mount(withFolders)
    await screen.findByRole('option', { name: 'Films · SFW · 12 videos' })
    fireEvent.change(await screen.findByRole('combobox', { name: 'Folder to upload' }), { target: { value: '3' } })
    expect(await screen.findByText(/only SFW channels can receive it/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Source folder path')).toBeNull()                         // the folder is already known: no path to type
    const dest = await screen.findByRole('combobox', { name: 'Destination channel' })
    await screen.findByRole('option', { name: 'Family films' })
    const options = Array.from((dest as HTMLSelectElement).options).map((o) => o.textContent)
    expect(options).toContain('Family films')
    expect(options).not.toContain('Adult archive')                                           // a channel holds one kind
    expect(options).not.toContain('Films')                                                   // and a folder is never a destination
    fireEvent.change(dest, { target: { value: '-1001' } })
    toRun()
    fireEvent.click(await screen.findByRole('button', { name: 'Upload scanned folder' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/local-folders/3/upload')?.body).toEqual({ destination: '-1001', keep_files: true }))
    expect(calls.some((c) => c.url === '/api/telegram-transfer/start')).toBe(false)
    expect(await screen.findByText(/Upload to Family films started: 11 videos with snapshots ready/)).toBeInTheDocument()
  })

  it('keeps the files unless deleting them is chosen, and says what that means', async () => {
    const calls = mount(withFolders)
    await screen.findByRole('option', { name: 'Films · SFW · 12 videos' })
    fireEvent.change(await screen.findByRole('combobox', { name: 'Folder to upload' }), { target: { value: '3' } })
    await screen.findByRole('option', { name: 'Family films' })
    fireEvent.change(await screen.findByRole('combobox', { name: 'Destination channel' }), { target: { value: '-1001' } })
    next(/Next: Options/)
    expect(screen.getByText(/uploaded as it is/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox', { name: /Delete local source after verified delivery/ }))
    expect(screen.getByRole('alert')).toHaveTextContent(/cannot be brought back/)
    next(/Next: Review/); next(/Next: Run/)
    fireEvent.click(await screen.findByRole('button', { name: 'Upload scanned folder' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/local-folders/3/upload')?.body).toEqual({ destination: '-1001', keep_files: false }))
  })

  it('needs a destination of its kind, and a folder is never listed as a Telegram source', async () => {
    mount(withFolders)
    await screen.findByRole('option', { name: 'Films · SFW · 12 videos' })
    fireEvent.change(await screen.findByRole('combobox', { name: 'Folder to upload' }), { target: { value: '3' } })
    expect(await screen.findByRole('button', { name: /Next: Options/ })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Telegram media' }))
    const group = await screen.findByRole('group', { name: 'Source channels' })
    expect(group).toHaveTextContent('Family films')
    expect(group).not.toHaveTextContent('Films ·')
    expect(Array.from(group.querySelectorAll('label')).map((l) => l.textContent).join('|')).not.toMatch(/(^|\|)Films(\||$)/)
  })
})

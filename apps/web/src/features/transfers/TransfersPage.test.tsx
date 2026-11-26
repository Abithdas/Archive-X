import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { TransfersPage } from './TransfersPage'

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(() => vi.unstubAllGlobals())

const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const channels = [{ telegram_id: '-1001', title: 'Main Archive', video_count: 18 }, { telegram_id: '-1002', title: 'Misc', video_count: 4 }]
type Handler = (url: string, init?: RequestInit) => Response | Promise<Response> | undefined
const mount = (extra: Handler = () => undefined, jobs: unknown[] = []) => {
  const calls: Array<{ url: string; body: unknown }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const x = await extra(url, init)
    if (x) return x
    if (url === '/api/telegram-transfer/lanes') return json({ governor: { transfer_active: 1, transfer_capacity: 3 }, resources: { background_active: 0, background_capacity: 2, media_workers: 2 }, jobs })
    if (url === '/api/source-channels') return json(channels)
    return json({})
  }))
  withRouter(<TransfersPage />)
  return calls
}

describe('Live lanes', () => {
  it('shows the resource chips and an empty state', async () => {
    mount()
    const chips = await screen.findByRole('list', { name: 'Resource usage' })
    expect(within(chips).getByText('1/3')).toBeInTheDocument()
    expect(await screen.findByText(/No active lane jobs/)).toBeInTheDocument()
  })
  it('says when the other mode has tasks running, and says nothing when it has none', async () => {
    const withOther = (other?: { count: number; running: number }) => (url: string) => (url === '/api/telegram-transfer/lanes' ? json({ governor: {}, resources: {}, jobs: [], other_mode_jobs: other }) : undefined)
    mount(withOther({ count: 2, running: 1 }))
    expect(await screen.findByRole('status', { name: 'Tasks in the other mode' })).toHaveTextContent('2 tasks are in the other mode, 1 of them running.')
    cleanup()
    mount(withOther({ count: 0, running: 0 }))
    await screen.findByText(/No active lane jobs/)
    expect(screen.queryByRole('status', { name: 'Tasks in the other mode' })).not.toBeInTheDocument()
  })
  it('lists a running lane job with progress and pauses it', async () => {
    const calls = mount((url) => (url.endsWith('/pause') ? json({ ok: true, status: 'PAUSED' }) : undefined), [{ id: 'j1', type: 'UPLOAD_LOCAL', status: 'RUNNING', operation: 'Uploading video', current_item: 'a.mp4', progress: 40 }])
    expect(await screen.findByText('Uploading a.mp4')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Uploading a.mp4 progress' })).toHaveAttribute('aria-valuenow', '40')
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/jobs/j1/pause')).toBe(true))
  })
  it('asks before stopping a job, and only then cancels it', async () => {
    const calls = mount((url) => (url.endsWith('/cancel') ? json({ ok: true, status: 'CANCELLED' }) : undefined), [{ id: 'j2', type: 'TELEGRAM_TRANSFER', status: 'RUNNING', progress: 10 }])
    fireEvent.click(await screen.findByRole('button', { name: 'Stop' }))
    expect(calls.some((c) => c.url.endsWith('/cancel'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(calls.some((c) => c.url.endsWith('/cancel'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
    fireEvent.click(screen.getByRole('button', { name: 'Stop job' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/jobs/j2/cancel')).toBe(true))
  })
})

describe('Metadata & Assets workflow', () => {
  const toRun = async () => {
    fireEvent.click(await screen.findByRole('checkbox', { name: /Main Archive/ }))
    fireEvent.click(screen.getByRole('button', { name: /Next: Processing Stages/ }))
    fireEvent.click(screen.getByRole('button', { name: /Next: Review/ }))
    fireEvent.click(screen.getByRole('button', { name: /Next: Run/ }))
  }
  const folderRows = { folders: [{ id: 3, path: 'D:\\Films', title: 'Films', content_rating: 'sfw', videos: 12, exists: true }, { id: 4, path: 'D:\\Scenes', title: 'Scenes', content_rating: 'nsfw', videos: 5, exists: true }], mode: 'sfw' }
  const withFolders = (more: Handler = () => undefined): Handler => (url, init) => {
    if (url === '/api/local-folders') return json(folderRows)
    if (url === '/api/source-channels') return json([...channels, { telegram_id: '-9000000000003', title: 'Films', role: 'local' }])
    return more(url, init)
  }
  it('lists local folders after the channels, and a folder is not a channel', async () => {
    mount(withFolders())
    const channelBox = await screen.findByRole('group', { name: 'Metadata and Assets channels' })
    const folderBox = await screen.findByRole('group', { name: 'Local folders to scan' })
    expect(channelBox).not.toHaveTextContent('Films')                                       // the folder's own channel row is not offered as a Telegram channel
    expect(folderBox).toHaveTextContent('Films')
    expect(folderBox).toHaveTextContent('Scenes')
    expect(channelBox.compareDocumentPosition(folderBox) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    fireEvent.click(screen.getByRole('checkbox', { name: /Films/ }))
    expect(screen.getByText('Selected: 1 folder')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Next: Processing Stages/ })).toBeEnabled()    // a folder alone is enough
  })
  it('scans only the chosen folders, without Telegram stages or preflight', async () => {
    const calls = mount(withFolders((url) => (url.startsWith('/api/local-folders/3/identify') ? json({ ok: true, kind: 'movies and shows' }) : undefined)))
    fireEvent.click(await screen.findByRole('checkbox', { name: /Films/ }))
    fireEvent.click(screen.getByRole('button', { name: /Next: Processing Stages/ }))
    fireEvent.click(screen.getByRole('button', { name: /Next: Review/ }))
    expect(screen.getByText('Local folders').closest('div')).toHaveTextContent('Films')
    fireEvent.click(screen.getByRole('button', { name: /Next: Run/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(await screen.findByText('Metadata scan started for Films.')).toBeInTheDocument()
    expect(calls.some((c) => c.url === '/api/local-folders/3/identify')).toBe(true)
    expect(calls.some((c) => c.url.includes('/api/metadata-assets/'))).toBe(false)
  })
  it('says why a folder could not be scanned and still scans the others', async () => {
    const calls = mount(withFolders((url) => (url === '/api/local-folders/3/identify' ? json({ detail: 'Add a TMDB key first (Settings → Metadata)' }, 400) : url === '/api/local-folders/4/identify' ? json({ ok: true }) : undefined)))
    fireEvent.click(await screen.findByRole('checkbox', { name: /Films/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: /Scenes/ }))
    fireEvent.click(screen.getByRole('button', { name: /Next: Processing Stages/ }))
    fireEvent.click(screen.getByRole('button', { name: /Next: Review/ }))
    fireEvent.click(screen.getByRole('button', { name: /Next: Run/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(await screen.findByText(/Metadata scan started for Scenes\. Films: Add a TMDB key first/)).toBeInTheDocument()
    expect(calls.filter((c) => c.url.endsWith('/identify')).map((c) => c.url)).toEqual(['/api/local-folders/3/identify', '/api/local-folders/4/identify'])
  })
  it('cannot leave the first step without a channel', async () => {
    mount()
    await screen.findByRole('checkbox', { name: /Main Archive/ })
    expect(screen.getByRole('button', { name: /Next: Processing Stages/ })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Select all' }))
    expect(screen.getByText('Selected: 2 channels')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(screen.getByText('Selected: 0 channels')).toBeInTheDocument()
  })
  it('runs preflight, then queues the recommended refresh with the right payload', async () => {
    const calls = mount((url) => (url.endsWith('/preflight') ? json({ ok: true, ready: true }) : url.endsWith('/process-channel') ? json({ ok: true, id: 'job9' }) : undefined))
    await toRun()
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/metadata-assets/process-channel')).toBe(true))
    const body = calls.find((c) => c.url === '/api/metadata-assets/process-channel')!.body as Record<string, unknown>
    expect(body).toMatchObject({ channel_ids: ['-1001'], stages: ['inventory', 'metadata', 'performers', 'channel_data', 'snapshots'], unbounded: true, force_metadata: false })
    expect(await screen.findByText(/queued as job job9: Inventory → Metadata & Entity Data → Build snapshots/)).toBeInTheDocument()
  })
  describe('the optional visual match (pHash)', () => {
    const toStages = async () => {
      fireEvent.click(await screen.findByRole('checkbox', { name: /Main Archive/ }))
      fireEvent.click(screen.getByRole('button', { name: /Next: Processing Stages/ }))
    }
    const run = async (calls: Array<{ url: string; body: unknown }>) => {
      fireEvent.click(screen.getByRole('button', { name: /Next: Review/ }))
      fireEvent.click(screen.getByRole('button', { name: /Next: Run/ }))
      fireEvent.click(screen.getByRole('button', { name: 'Run' }))
      await waitFor(() => expect(calls.some((c) => c.url === '/api/metadata-assets/process-channel')).toBe(true))
      return calls.find((c) => c.url === '/api/metadata-assets/process-channel')!.body as Record<string, unknown>
    }
    const handler: Handler = (url) => (url.endsWith('/preflight') ? json({ ok: true, ready: true }) : url.endsWith('/process-channel') ? json({ ok: true, id: 'job9' }) : undefined)

    it('is on by default and a scan asks for it', async () => {
      const calls = mount(handler)
      await toStages()
      expect(screen.getByRole('checkbox', { name: /Visual match \(pHash\)/ })).toBeChecked()
      expect((await run(calls)).visual_match).toBe(true)
    })

    it('can be switched off, and then the scan says so', async () => {
      const calls = mount(handler)
      await toStages()
      fireEvent.click(screen.getByRole('checkbox', { name: /Visual match \(pHash\)/ }))
      expect(screen.getByRole('checkbox', { name: /Visual match \(pHash\)/ })).not.toBeChecked()
      expect((await run(calls)).visual_match).toBe(false)
    })

    it('is shown as off and cannot be chosen without the metadata stage it belongs to', async () => {
      mount(handler)
      await toStages()
      fireEvent.click(screen.getByRole('checkbox', { name: /^Metadata & Entity Data/ }))
      const box = screen.getByRole('checkbox', { name: /Visual match \(pHash\)/ })
      expect(box).toBeDisabled()
      expect(box).not.toBeChecked()
    })

    it('is part of the review before the run', async () => {
      mount(handler)
      await toStages()
      fireEvent.click(screen.getByRole('checkbox', { name: /Visual match \(pHash\)/ }))
      fireEvent.click(screen.getByRole('button', { name: /Next: Review/ }))
      const row = screen.getByText('Visual match (pHash)').closest('div')!
      expect(within(row).getByText('No')).toBeInTheDocument()
    })
  })
  it('offers to add required stages when preflight says they are missing', async () => {
    const calls = mount((url) => (url.endsWith('/preflight') ? json({ ok: true, ready: false, required_stages: ['inventory'], missing_by_channel: [{ channel_id: '-1001', missing: ['inventory'] }] }) : url.endsWith('/process-channel') ? json({ ok: true }) : undefined))
    await toRun()
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(await screen.findByText('Add the required stage(s) before running the selected stages?')).toBeInTheDocument()
    expect(calls.some((c) => c.url.endsWith('/process-channel'))).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Add stages and continue' }))
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/process-channel'))).toBe(true))
    expect((calls.find((c) => c.url.endsWith('/process-channel'))!.body as Record<string, unknown>).auto_added_prerequisites).toEqual({ inventory: ['-1001'] })
  })
  it('reports a failed preflight without starting anything', async () => {
    const calls = mount((url) => (url.endsWith('/preflight') ? json({ ok: false, detail: 'Telegram is not connected' }) : undefined))
    await toRun()
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(await screen.findByText(/Could not validate prerequisites: Telegram is not connected/)).toBeInTheDocument()
    expect(calls.some((c) => c.url.endsWith('/process-channel'))).toBe(false)
  })
  const imageOnly = async (scan: boolean, reconcile: boolean) => {
    fireEvent.click(await screen.findByRole('checkbox', { name: /Main Archive/ }))
    fireEvent.click(screen.getByRole('button', { name: /Next: Processing Stages/ }))
    for (const name of ['Inventory', 'Metadata & Entity Data', 'Snapshots']) fireEvent.click(screen.getByRole('checkbox', { name: new RegExp(`^${name}`) }))
    if (scan) fireEvent.click(screen.getByRole('checkbox', { name: /^Image scan/ }))
    if (reconcile) fireEvent.click(screen.getByRole('checkbox', { name: /^Reconcile deleted Telegram images/ }))
    fireEvent.click(screen.getByRole('button', { name: /Next: Review/ }))
    fireEvent.click(screen.getByRole('button', { name: /Next: Run/ }))
  }
  it('an image scan alone queues only the photo job, without video stages or preflight', async () => {
    const calls = mount((url) => (url === '/api/photos/scan' ? json({ ok: true, id: 'p1' }) : undefined))
    await imageOnly(true, false)
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(await screen.findByText('Image scan queued as job p1 for 1 selected channel; it scans full Telegram history without modifying messages.')).toBeInTheDocument()
    expect(calls.find((c) => c.url === '/api/photos/scan')!.body).toEqual({ channel_ids: ['-1001'] })
    expect(calls.some((c) => c.url.endsWith('/preflight') || c.url.endsWith('/process-channel'))).toBe(false)
  })
  it('scan plus reconcile is one job, so the scan finishes before reconciliation', async () => {
    const calls = mount((url) => (url === '/api/photos/reconcile' ? json({ ok: true, id: 'p2', also_scan: true }) : undefined))
    await imageOnly(true, true)
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(await screen.findByText(/Image scan followed by deleted-image reconciliation queued as job p2/)).toBeInTheDocument()
    expect(calls.find((c) => c.url === '/api/photos/reconcile')!.body).toEqual({ channel_ids: ['-1001'], also_scan: true }); expect(calls.some((c) => c.url === '/api/photos/scan')).toBe(false)
  })
  it('reconcile alone checks already-indexed images only', async () => {
    const calls = mount((url) => (url === '/api/photos/reconcile' ? json({ ok: true, id: 'p3' }) : undefined))
    await imageOnly(false, true)
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(await screen.findByText(/Deleted Telegram image reconciliation queued as job p3/)).toBeInTheDocument(); expect(calls.find((c) => c.url === '/api/photos/reconcile')!.body).toEqual({ channel_ids: ['-1001'] })
  })
  it('says why the image job could not be queued', async () => {
    mount((url) => (url === '/api/photos/scan' ? json({ ok: false, detail: 'Telegram is not connected' }, 503) : undefined))
    await imageOnly(true, false)
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(await screen.findByText('Image scan could not be queued: Telegram is not connected')).toBeInTheDocument()
  })
  it('video stages and an image scan both start, and the message says so', async () => {
    const calls = mount((url) => (url.endsWith('/preflight') ? json({ ok: true, ready: true }) : url.endsWith('/process-channel') ? json({ ok: true, id: 'job9' }) : url === '/api/photos/scan' ? json({ ok: true, id: 'p1' }) : undefined))
    fireEvent.click(await screen.findByRole('checkbox', { name: /Main Archive/ }))
    fireEvent.click(screen.getByRole('button', { name: /Next: Processing Stages/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: /^Image scan/ }))
    fireEvent.click(screen.getByRole('button', { name: /Next: Review/ })); fireEvent.click(screen.getByRole('button', { name: /Next: Run/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(await screen.findByText(/queued as job job9: .*\. Image scan queued as job p1/)).toBeInTheDocument()
    expect(calls.some((c) => c.url === '/api/photos/scan')).toBe(true)
  })
  it('Run Pending skips inventory and never scans', async () => {
    const calls = mount((url) => (url.endsWith('/process-pending') ? json({ ok: true, no_pending: true, message: 'No pending Library items.' }) : undefined))
    await toRun()
    fireEvent.click(screen.getByRole('button', { name: 'Run Pending (no scan)' }))
    await waitFor(() => expect(calls.some((c) => c.url.endsWith('/process-pending'))).toBe(true))
    expect((calls.find((c) => c.url.endsWith('/process-pending'))!.body as { stages: string[] }).stages).not.toContain('inventory')
    expect(calls.some((c) => c.url.endsWith('/preflight'))).toBe(false)
  })
})

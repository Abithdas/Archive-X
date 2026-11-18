import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LocalFolders } from './LocalFolders'
import { withRouter } from '@/test/withRouter'

HTMLDialogElement.prototype.showModal ||= function (this: HTMLDialogElement) { this.setAttribute('open', '') }
HTMLDialogElement.prototype.close ||= function (this: HTMLDialogElement) { this.removeAttribute('open') }
afterEach(() => vi.unstubAllGlobals())

const folder = { id: 1, path: 'E:\\Pictures', title: 'Pictures', channel_id: '-9000000000001', recursive: 1, excluded: [], in_library: 1, content_rating: 'nsfw', holds: 'both', last_status: 'COMPLETE', last_error: '', last_scan_at: null, photos: 12, videos: 0, exists: true, scan: {} }

function mock(folders: object[]) {
  const calls: Array<{ url: string; method: string; body?: Record<string, unknown> }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (url.startsWith('/api/local-folders/browse')) return new Response(JSON.stringify({ path: decodeURIComponent(url.split('path=')[1] ?? ''), parent: '', folders: [{ name: 'Holiday', path: 'E:\\Holiday' }] }), { status: 200 })
    if (url === '/api/local-folders' && method === 'GET') return new Response(JSON.stringify({ ok: true, mode: 'sfw', folders }), { status: 200 })
    if (url === '/api/content-mode') return new Response(JSON.stringify({ ok: true, mode: 'sfw', pin_set: false, locked_for: 0 }), { status: 200 })
    return new Response('{"ok":true,"id":2}', { status: 200 })
  }))
  return calls
}

describe('LocalFolders', () => {
  it('lists the folders with what they hold and lets them be marked', async () => {
    const calls = mock([folder])
    withRouter(<LocalFolders />)
    expect(await screen.findByText('12 photos')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'SFW' }))
    expect(await screen.findByText('1 change not saved')).toBeInTheDocument()
    expect(calls.some((c) => c.url === '/api/telegram/channel-marks' && c.method === 'POST')).toBe(false)   // kept aside until Save
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/telegram/channel-marks' && c.method === 'POST')?.body).toEqual({ ids: ['-9000000000001'], content_rating: 'sfw' }))
  })
  it('says when there are none, and adds a chosen folder with its mark', async () => {
    const calls = mock([])
    withRouter(<LocalFolders />)
    expect(await screen.findByText(/No folders yet/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Add a folder' }))
    fireEvent.change(await screen.findByLabelText('Folder path'), { target: { value: 'E:\\' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Holiday' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Add this folder' })).toBeEnabled())   // once the chosen folder has been opened
    fireEvent.click(screen.getByRole('button', { name: 'Add this folder' }))
    await waitFor(() => expect(calls.find((c) => c.url === '/api/local-folders' && c.method === 'POST')?.body).toMatchObject({ content_rating: 'sfw', holds: 'both' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Add this folder' })).toBeNull())   // the dialog closes by itself once the folder is added
  })
  it('leaves upload and metadata scans to the Transfers page', async () => {
    mock([{ ...folder, videos: 4 }])
    withRouter(<LocalFolders />)
    await screen.findByText(folder.title ?? folder.path)
    for (const name of ['Upload to Telegram', 'Scan metadata', 'Check again']) expect(screen.queryByRole('button', { name })).toBeNull()
    expect(screen.getByRole('button', { name: 'Remove' })).toBeInTheDocument()
  })
  it('says how many folders belong to the other mode, in either mode', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url === '/api/local-folders') return new Response(JSON.stringify({ ok: true, mode: 'nsfw', folders: [], hidden: 2 }), { status: 200 })
      if (url === '/api/content-mode') return new Response(JSON.stringify({ ok: true, mode: 'nsfw', pin_set: true, locked_for: 0 }), { status: 200 })
      return new Response('{"ok":true,"workers":2}', { status: 200 })
    }))
    withRouter(<LocalFolders />)
    expect(await screen.findByText(/2 folders are marked SFW and not listed in NSFW mode/)).toBeInTheDocument()
  })
})

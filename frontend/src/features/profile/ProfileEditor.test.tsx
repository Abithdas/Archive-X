import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { ProfileEditor } from './ProfileEditor'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(() => vi.unstubAllGlobals())
const json = (b: unknown, code = 200) => new Response(JSON.stringify(b), { status: code })
const PROFILE = { name: 'Ava', username: 'ava', custom_name: 'Ava', telegram_name: 'Ava T', has_custom_image: false, image_version: '' }
const mount = (save: (i?: RequestInit) => Response = () => json({ ok: true, ...PROFILE, custom_name: 'Zed', name: 'Zed', has_custom_image: true, image_version: '9' })) => {
  const calls: Array<{ url: string; method: string; body?: Record<string, unknown> }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (url === '/api/account/profile' && init?.method === 'POST') return save(init)
    if (url === '/api/account/profile') return json(PROFILE)
    return json({})
  }))
  const onClose = vi.fn()
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  qc.setQueryData(['profile'], PROFILE)
  render(<QueryClientProvider client={qc}><ProfileEditor onClose={onClose} /></QueryClientProvider>)
  return { calls, onClose, qc }
}
const posts = <T extends { method: string }>(c: T[]) => c.filter((x) => x.method === 'POST')

describe('ProfileEditor', () => {
  it('closes without a request when nothing changed', () => {
    const { calls, onClose } = mount()
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(onClose).toHaveBeenCalled(); expect(posts(calls)).toHaveLength(0)
  })
  it('saves a new name, updates the cached profile and closes', async () => {
    const { calls, onClose, qc } = mount()
    fireEvent.change(screen.getByLabelText('Display name'), { target: { value: ' Zed ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(posts(calls)[0]!.body).toEqual({ name: 'Zed' }); expect((qc.getQueryData(['profile']) as { name: string }).name).toBe('Zed')
  })
  it('sends a chosen photo as data, and rejects one that is too big or the wrong type', async () => {
    const { calls } = mount()
    const input = screen.getByLabelText('Choose a photo file') as HTMLInputElement
    const big = new File([new Uint8Array(9 * 1024 * 1024)], 'big.png', { type: 'image/png' })
    fireEvent.change(input, { target: { files: [big] } }); expect(await screen.findByText('Photo must be 8 MiB or smaller.')).toBeInTheDocument()
    fireEvent.change(input, { target: { files: [new File(['x'], 'a.svg', { type: 'image/svg+xml' })] } }); expect(await screen.findByText('Choose a PNG, JPEG, WebP or GIF image.')).toBeInTheDocument()
    fireEvent.change(input, { target: { files: [new File(['abc'], 'me.png', { type: 'image/png' })] } })
    expect(await screen.findByText('Selected me.png. Press Save to apply it.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(posts(calls)).toHaveLength(1))
    expect(posts(calls)[0]!.body).toMatchObject({ name: 'Ava', image_data: expect.stringMatching(/^data:image\/png;base64,/) })
  })
  it('can go back to the Telegram photo', async () => {
    const { calls } = mount()
    fireEvent.click(screen.getByRole('button', { name: 'Use Telegram photo' }))
    expect(screen.getByText('Saving will use your Telegram profile photo again.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(posts(calls)[0]!.body).toEqual({ name: 'Ava', reset_image: true }))
  })
  it('shows the server\'s reason and stays open when saving fails', async () => {
    const { onClose } = mount(() => json({ detail: 'Display name must be 64 characters or fewer' }, 400))
    fireEvent.change(screen.getByLabelText('Display name'), { target: { value: 'New' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('Display name must be 64 characters or fewer')).toBeInTheDocument(); expect(onClose).not.toHaveBeenCalled()
  })
})

import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BulkAddToPerson, BulkDelete, SelectionBar, removalOptions } from './PhotoBulk'
import { withRouter } from '@/test/withRouter'
import type { Photo } from './photos'

HTMLDialogElement.prototype.showModal ||= function (this: HTMLDialogElement) { this.setAttribute('open', '') }
HTMLDialogElement.prototype.close ||= function (this: HTMLDialogElement) { this.removeAttribute('open') }
vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'content-type': 'application/json' } })
const photo = (n: number, local = false) => ({ id: n, channel_id: local ? '-900' : '-100', message_id: n, filename: `p${n}.jpg`, source_kind: local ? 'local' : 'telegram' }) as unknown as Photo
afterEach(() => vi.unstubAllGlobals())

describe('removalOptions', () => {
  it('offers only the ways that fit the photos: files for folder photos, Telegram for Telegram photos', () => {
    expect(removalOptions([photo(1)]).map((o) => o.mode)).toEqual(['archive', 'telegram'])
    expect(removalOptions([photo(1, true)]).map((o) => o.mode)).toEqual(['archive', 'file'])
    const both = removalOptions([photo(1), photo(2, true), photo(3, true)])
    expect(both.map((o) => [o.mode, o.count])).toEqual([['archive', 3], ['file', 2], ['telegram', 1]])
  })
})

describe('SelectionBar', () => {
  it('says how many are selected and offers every action', async () => {
    const h = { onAll: vi.fn(), onNone: vi.fn(), onPerson: vi.fn(), onDelete: vi.fn(), onDone: vi.fn() }
    render(<SelectionBar count={2} loaded={10} {...h} />)
    expect(await screen.findByText('2 selected')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Select all 10 shown' })); expect(h.onAll).toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /Add to a person/ })); expect(h.onPerson).toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /Delete/ })); expect(h.onDelete).toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Done' })); expect(h.onDone).toHaveBeenCalled()
  })
  it('offers Clear when everything shown is selected', async () => {
    render(<SelectionBar count={10} loaded={10} onAll={vi.fn()} onNone={vi.fn()} onPerson={vi.fn()} onDelete={vi.fn()} onDone={vi.fn()} />)
    expect(await screen.findByRole('button', { name: 'Clear' })).toBeInTheDocument()
  })
})

function render(ui: React.ReactElement) { return withRouter(ui) }

describe('BulkDelete', () => {
  it('removes each selected photo in turn with the chosen way and reports which were removed', async () => {
    const calls: { url: string; body: unknown }[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => { calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined }); return json({ ok: true }) }))
    const done = vi.fn(), close = vi.fn()
    render(<BulkDelete photos={[photo(1), photo(2)]} onClose={close} onDone={done} />)
    expect(await screen.findByRole('button', { name: 'Choose one' })).toBeDisabled()          // nothing happens until a way is chosen
    fireEvent.click(await screen.findByRole('radio', { name: /Remove from Archive X/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(done).toHaveBeenCalledWith(['-100:1', '-100:2']))
    expect(calls.filter((c) => c.url.includes('/delete')).map((c) => [c.url, c.body])).toEqual([
      ['/api/photos/-100/1/delete', { scope: 'archive_only' }], ['/api/photos/-100/2/delete', { scope: 'archive_only' }]])
    expect(close).toHaveBeenCalled()
  })
  it('keeps going when one fails and does not report the failed one as removed', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => (url.includes('/1/delete') ? json({ ok: false, detail: 'locked' }) : json({ ok: true }))))
    const done = vi.fn()
    render(<BulkDelete photos={[photo(1), photo(2)]} onClose={vi.fn()} onDone={done} />)
    fireEvent.click(await screen.findByRole('radio', { name: /Remove from Archive X/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(done).toHaveBeenCalledWith(['-100:2']))
  })
})

describe('BulkAddToPerson', () => {
  it('adds the one face of each photo and leaves out the photos without exactly one', async () => {
    const moves: unknown[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes('/of-photo')) { const m = Number(/message_id=(\d+)/.exec(url)![1]); return json({ faces: m === 1 ? [{ detection_id: 11 }] : m === 2 ? [{ detection_id: 21 }, { detection_id: 22 }] : [] }) }
      if (url.includes('/detections/move')) { moves.push(JSON.parse(String(init!.body))); return json({ ok: true }) }
      if (url.includes('/performers?')) return json({ items: [{ id: 5, name: 'Jane Doe' }] })
      if (url.includes('provider-search')) return json({ items: [], configured: true })
      return json({})
    }))
    const done = vi.fn()
    render(<BulkAddToPerson photos={[photo(1), photo(2), photo(3)]} onClose={vi.fn()} onDone={done} />)
    fireEvent.change(await screen.findByRole('searchbox'), { target: { value: 'Jane' } })
    fireEvent.click(await screen.findByText('Jane Doe'))
    await waitFor(() => expect(moves).toEqual([{ detection_ids: [11], performer_id: 5 }]))   // photo 2 has two faces, photo 3 none: left out
    expect(done).toHaveBeenCalled()
  })
})

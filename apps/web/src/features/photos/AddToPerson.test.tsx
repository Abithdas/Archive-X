import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { AddToPerson } from './AddToPerson'
import type { Photo } from './photos'

HTMLDialogElement.prototype.showModal ||= function (this: HTMLDialogElement) { this.setAttribute('open', '') }
HTMLDialogElement.prototype.close ||= function (this: HTMLDialogElement) { this.removeAttribute('open') }
vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
afterEach(() => vi.unstubAllGlobals())

const photo = { id: 9, channel_id: '-1001', message_id: 44, filename: 'IMG_9.jpg' } as unknown as Photo
const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200 })

function mock(faces: unknown[]) {
  const posts: Array<{ url: string; body: unknown }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.startsWith('/api/photos/faces/of-photo')) return json({ ok: true, faces })
    if (url.startsWith('/api/photos/faces/performers?')) return json({ items: [{ id: 3, name: 'Ava Stone' }] })
    if (url.startsWith('/api/photos/faces/performers/provider-search')) return json({ items: [], configured: false })
    if (init?.method === 'POST') { posts.push({ url, body: JSON.parse(String(init.body)) }); return json({ ok: true, moved: 1 }) }
    return json({})
  }))
  return posts
}

describe('AddToPerson', () => {
  it('says so when no face has been found in the photo yet', async () => {
    mock([])
    withRouter(<AddToPerson photo={photo} onClose={() => undefined} />)
    expect(await screen.findByText(/No face has been found in this photo yet/)).toBeInTheDocument()
  })

  it('names the only face after the chosen person, with no face to choose first', async () => {
    const posts = mock([{ detection_id: 70, crop_url: '/c/70', performer_id: null, performer_name: '' }])
    const onClose = vi.fn()
    withRouter(<AddToPerson photo={photo} onClose={onClose} />)
    fireEvent.change(await screen.findByLabelText('Performer name'), { target: { value: 'Ava' } })
    fireEvent.click(await screen.findByText('Ava Stone'))
    await waitFor(() => expect(posts).toEqual([{ url: '/api/photos/faces/detections/move', body: { detection_ids: [70], performer_id: 3 } }]))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
  })

  it('with several faces asks which one first', async () => {
    mock([{ detection_id: 70, crop_url: '/c/70', performer_id: null, performer_name: '' }, { detection_id: 71, crop_url: '/c/71', performer_id: 3, performer_name: 'Ava Stone' }])
    withRouter(<AddToPerson photo={photo} onClose={() => undefined} />)
    expect(await screen.findByText('Which face?')).toBeInTheDocument()
    expect(screen.queryByLabelText('Performer name')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Face, not named yet' }))
    expect(await screen.findByLabelText('Performer name')).toBeInTheDocument()
  })
})

import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ContentSwitch } from './ContentSwitch'
import { withRouter } from '@/test/withRouter'

HTMLDialogElement.prototype.showModal ||= function (this: HTMLDialogElement) { this.setAttribute('open', '') }
HTMLDialogElement.prototype.close ||= function (this: HTMLDialogElement) { this.removeAttribute('open') }
afterEach(() => vi.unstubAllGlobals())

function api(state: { mode: string; pin_set: boolean }, post: (body: Record<string, unknown>) => Response) {
  const posts: Array<Record<string, unknown>> = []
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
    if ((init?.method ?? 'GET') === 'POST') { const b = JSON.parse(String(init?.body)); posts.push(b); return post(b) }
    return new Response(JSON.stringify({ ok: true, locked_for: 0, ...state }), { status: 200 })
  }))
  return posts
}
const ok = (mode: string) => new Response(JSON.stringify({ ok: true, mode, pin_set: true }), { status: 200 })

describe('ContentSwitch', () => {
  it('switches to SFW without asking anything', async () => {
    const posts = api({ mode: 'nsfw', pin_set: true }, () => ok('sfw'))
    withRouter(<ContentSwitch />)
    await waitFor(() => expect(screen.getByTestId('content-switch')).toHaveAttribute('data-ready', 'true'))
    fireEvent.click(screen.getByRole('button', { name: 'SFW' }))
    await waitFor(() => expect(posts).toEqual([{ mode: 'sfw' }]))
  })
  it('asks for the PIN before NSFW and says when it is wrong', async () => {
    const posts = api({ mode: 'sfw', pin_set: true }, (b) => (b.pin === '2468' ? ok('nsfw') : new Response('{"detail":"Wrong PIN"}', { status: 403 })))
    withRouter(<ContentSwitch />)
    await waitFor(() => expect(screen.getByTestId('content-switch')).toHaveAttribute('data-ready', 'true'))
    fireEvent.click(screen.getByRole('button', { name: 'NSFW' }))
    fireEvent.change(await screen.findByLabelText('PIN'), { target: { value: '1111' } })
    fireEvent.click(screen.getByRole('button', { name: 'Switch to NSFW' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Wrong PIN')
    fireEvent.change(screen.getByLabelText('PIN'), { target: { value: '2468' } })
    fireEvent.click(screen.getByRole('button', { name: 'Switch to NSFW' }))
    await waitFor(() => expect(posts.at(-1)).toEqual({ mode: 'nsfw', pin: '2468' }))
  })
  it('with no PIN chosen, NSFW switches on at once: the PIN is optional', async () => {
    const posts = api({ mode: 'sfw', pin_set: false }, () => ok('nsfw'))
    withRouter(<ContentSwitch />)
    await waitFor(() => expect(screen.getByTestId('content-switch')).toHaveAttribute('data-ready', 'true'))
    fireEvent.click(screen.getByRole('button', { name: 'NSFW' }))
    await waitFor(() => expect(posts).toEqual([{ mode: 'nsfw' }]))
    expect(screen.queryByLabelText('New PIN')).toBeNull()
  })
})

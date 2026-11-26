import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { withRouter } from '@/test/withRouter'
import { groupPatches, withPatch } from './content'
import { ChannelMarks } from './ChannelMarks'

afterEach(() => vi.unstubAllGlobals())
const channels = Array.from({ length: 12 }, (_, i) => ({ kind: 'telegram', telegram_id: `-100${i}`, title: `Channel ${i + 1}`, username: null, in_library: 1, content_rating: 'nsfw', holds: 'both', videos: 3, photos: 1 }))

describe('ChannelMarks', () => {
  it('lets many channels be selected at once', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: true, mode: 'sfw', channels }), { status: 200 })))
    withRouter(<ChannelMarks />)
    await screen.findByLabelText('Select Channel 1')
    for (let i = 1; i <= 9; i++) fireEvent.click(screen.getByLabelText(`Select Channel ${i}`))
    expect(screen.getByText('9 selected:')).toBeInTheDocument()
  })

  it('keeps changes aside, saves them in one request per kind of change, and can discard', async () => {
    const calls: { url: string; method: string; body: unknown }[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? 'GET', body: init?.body ? JSON.parse(String(init.body)) : undefined })
      return new Response(JSON.stringify(init?.method === 'POST' ? { changed: 3 } : { ok: true, mode: 'sfw', channels, hidden: 2 }), { status: 200 })
    }))
    withRouter(<ChannelMarks />)
    expect(await screen.findByText(/2 channels are marked NSFW \(or not sorted yet\) and not listed in SFW mode/)).toBeInTheDocument()
    for (const i of [1, 2, 3]) fireEvent.click(screen.getByLabelText(`Select Channel ${i}`))
    fireEvent.click(screen.getByRole('button', { name: 'Mark SFW' }))
    expect(screen.getByText('3 changes not saved')).toBeInTheDocument()
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
    await waitFor(() => expect(screen.queryByText('3 changes not saved')).toBeNull())
    fireEvent.click(screen.getByRole('button', { name: 'Mark SFW' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1))
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ ids: ['-1000', '-1001', '-1002'], content_rating: 'sfw' })
  })

  it('hides unnamed channels until asked, and selects a run with shift', async () => {
    const list = [...channels.slice(0, 4), { ...channels[4], title: 'Unknown Channel', unnamed: true }]
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: true, mode: 'sfw', channels: list }), { status: 200 })))
    withRouter(<ChannelMarks />)
    await screen.findByLabelText('Select Channel 1')
    expect(screen.queryByLabelText('Select Unknown Channel')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Show 1 unnamed' }))
    expect(screen.getByLabelText('Select Unknown Channel')).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Select Channel 1'))
    fireEvent.click(screen.getByLabelText('Select Channel 3'), { shiftKey: true })
    expect(screen.getByText('3 selected:')).toBeInTheDocument()
  })
})

describe('draft helpers', () => {
  const saved = { in_library: true, content_rating: 'nsfw' as const, holds: 'both' }
  it('drops a change that goes back to what is saved', () => {
    const one = withPatch({}, 'a', saved, { content_rating: 'sfw' })
    expect(one).toEqual({ a: { content_rating: 'sfw' } })
    expect(withPatch(one, 'a', saved, { content_rating: 'nsfw' })).toEqual({})
  })
  it('groups equal changes so forty channels are one request', () => {
    const groups = groupPatches({ a: { content_rating: 'sfw' }, b: { content_rating: 'sfw' }, c: { in_library: false } })
    expect(groups).toEqual([{ ids: ['a', 'b'], patch: { content_rating: 'sfw' } }, { ids: ['c'], patch: { in_library: false } }])
  })
})

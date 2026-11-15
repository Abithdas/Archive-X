import { act, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Entity } from '@/api/types'
import { useSearch } from '@/app/search'
import { withRouter } from '@/test/withRouter'
import { EntityDirectory } from './EntityDirectory'
import { filterEntities, pageCount } from './filter'

const e = (id: number, name: string, video_count: number, gender?: string): Entity => ({ id, name, video_count, gender, image: '' })
const data = [e(1, 'Zoë Park', 3, 'Female'), e(2, 'Adam Reed', 9, 'Male'), e(3, 'Ava Stone', 9, 'Female')]

describe('filterEntities', () => {
  it('orders by video count then name', () => expect(filterEntities(data, '').map((x) => x.id)).toEqual([2, 3, 1]))
  it('ignores case and accents when searching', () => expect(filterEntities(data, 'zoe').map((x) => x.id)).toEqual([1]))
  it('filters by gender', () => expect(filterEntities(data, '', 'male').map((x) => x.id)).toEqual([2]))
  it('pages', () => { expect(pageCount(0, 48)).toBe(1); expect(pageCount(49, 48)).toBe(2) })
})

const mount = (kind: 'performers' | 'studios') => withRouter(<EntityDirectory kind={kind} />)

afterEach(() => { vi.unstubAllGlobals(); useSearch.getState().set('') })

describe('EntityDirectory', () => {
  it('shows a skeleton, then the cards, and filters by search', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(data), { status: 200 })))
    mount('performers')
    expect(await screen.findByRole('status')).toBeInTheDocument()
    expect(await screen.findByRole('article', { name: 'Ava Stone' })).toBeInTheDocument()
    expect(screen.queryByRole('article', { name: 'Adam Reed' })).not.toBeInTheDocument()   // opens on Female
    fireEvent.click(screen.getByRole('button', { name: 'All' }))
    act(() => useSearch.getState().set('adam'))
    expect(screen.queryByRole('article', { name: 'Ava Stone' })).not.toBeInTheDocument()
    expect(screen.getByRole('article', { name: 'Adam Reed' })).toBeInTheDocument()
  })
  it('links performer cards to their profile', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(data), { status: 200 })))
    mount('performers')
    fireEvent.click(await screen.findByRole('button', { name: 'Male' }))
    expect((await screen.findByRole('link', { name: 'Adam Reed' })).getAttribute('href')).toBe('/performers/2')
  })
  it('has no Trans choice; a trans performer is not under Female or Male and is still under All', async () => {
    const list = [e(1, 'Ava Stone', 5, 'female'), e(2, 'Tia Lane', 4, 'transgender_female')]
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(list), { status: 200 })))
    mount('performers')
    await screen.findByRole('article', { name: 'Ava Stone' })
    expect(screen.queryByRole('button', { name: 'Trans' })).not.toBeInTheDocument()
    expect(screen.queryByRole('article', { name: 'Tia Lane' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'All' }))
    expect(await screen.findByRole('article', { name: 'Tia Lane' })).toBeInTheDocument()
  })
  it('only offers the gender filter for performers', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('[]', { status: 200 })))
    mount('studios')
    expect(await screen.findByText('No studios yet.')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Gender' })).not.toBeInTheDocument()
  })
  it('offers Retry when loading fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"boom"}', { status: 500 })))
    mount('performers')
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})

describe('studio directory', () => {
  const studios = Array.from({ length: 13 }, (_, i) => e(i + 1, `Studio ${String(i + 1).padStart(2, '0')}`, 20 - i))
  it('lists studios as tiles with a link and a video count', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(studios), { status: 200 })))
    mount('studios')
    const tile = await screen.findByRole('article', { name: 'Studio 01' })
    expect(within(tile).getByRole('link', { name: 'Studio 01' })).toHaveAttribute('href', '/studios/1')
    expect(within(tile).getByText('20 videos')).toBeInTheDocument()
  })
  it('pages 12 at a time, with the page box and the items-per-page choice', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(studios), { status: 200 })))
    mount('studios')
    await screen.findByRole('article', { name: 'Studio 01' })
    expect(screen.getAllByRole('article')).toHaveLength(12)
    expect(screen.queryByRole('article', { name: 'Studio 13' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(await screen.findByRole('article', { name: 'Studio 13' })).toBeInTheDocument()
    expect(screen.getAllByRole('article')).toHaveLength(1)
    fireEvent.change(screen.getByLabelText('Items per page'), { target: { value: '24' } })
    expect(await screen.findAllByRole('article')).toHaveLength(13)
  })
})

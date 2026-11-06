import { fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Entity } from '@/api/types'
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

afterEach(() => vi.unstubAllGlobals())

describe('EntityDirectory', () => {
  it('shows a skeleton, then the cards, and filters by search', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(data), { status: 200 })))
    mount('performers')
    expect(await screen.findByRole('status')).toBeInTheDocument()
    expect(await screen.findByRole('article', { name: 'Ava Stone' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Search performers'), { target: { value: 'adam' } })
    expect(screen.queryByRole('article', { name: 'Ava Stone' })).not.toBeInTheDocument()
    expect(screen.getByRole('article', { name: 'Adam Reed' })).toBeInTheDocument()
  })
  it('links performer cards to their profile', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(data), { status: 200 })))
    mount('performers')
    expect((await screen.findByRole('link', { name: 'Adam Reed' })).getAttribute('href')).toBe('/performers/2')
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

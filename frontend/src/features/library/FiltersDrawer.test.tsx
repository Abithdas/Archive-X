import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { FiltersDrawer, isoDay, rangeEndingToday } from './FiltersDrawer'
import { NO_FILTERS, useLibraryStore } from './store'

beforeAll(() => {
  // jsdom has no <dialog> modal API
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(() => { vi.unstubAllGlobals(); useLibraryStore.setState({ filters: NO_FILTERS }) })

describe('date helpers', () => {
  it('formats a local calendar day', () => expect(isoDay(new Date(2026, 0, 5))).toBe('2026-01-05'))
  it('ends today and starts N days earlier, across a month boundary', () =>
    expect(rangeEndingToday(7, new Date(2026, 2, 3))).toEqual({ dateFrom: '2026-02-24', dateTo: '2026-03-03' }))
})

describe('FiltersDrawer', () => {
  const mount = () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('[]', { status: 200 })))
    return render(<QueryClientProvider client={new QueryClient()}><FiltersDrawer open onClose={() => {}} /></QueryClientProvider>)
  }
  it('has every classic control: studio, performer, tag, dates, quick ranges and resolution chips', () => {
    mount()
    for (const ph of ['Any studio…', 'Any performer…', 'Any tag…']) expect(screen.getByPlaceholderText(ph)).toBeInTheDocument()
    expect(screen.getByLabelText('Upload date from')).toBeInTheDocument()
    for (const n of ['7 days', '30 days', '1 year', 'Any', '4K', '1440p', '1080p', '720p', '<720p']) expect(screen.getByRole('button', { name: n })).toBeInTheDocument()
  })
  it('applies resolution and a quick range together', () => {
    mount()
    fireEvent.click(screen.getByRole('button', { name: '1080p' }))
    fireEvent.click(screen.getByRole('button', { name: '30 days' }))
    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }))
    const f = useLibraryStore.getState().filters
    expect(f.resolution).toBe('1080')
    expect(f.dateTo).toBe(isoDay(new Date()))
    expect(f.dateFrom).not.toBe('')
  })
})

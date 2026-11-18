import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CaptionModel, speedText } from './CaptionModel'

vi.mock('sonner', () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }), Toaster: () => null }))
afterEach(() => vi.unstubAllGlobals())
const CHOICES = [{ id: 'small', label: 'Fast', size: '460 MB', note: 'Runs well on any PC.' }, { id: 'large-v3-turbo', label: 'Accurate', size: '1.6 GB', note: 'The best words.' }]

describe('CaptionModel', () => {
  it('lists the models, says what the chosen one is like and how fast it is, and changes it', async () => {
    const calls: Array<{ url: string; body?: unknown }> = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      return new Response(JSON.stringify({ ok: true, model: 'small', choices: CHOICES, speed: 7.6, state: 'ready', device: 'cpu' }), { status: 200 })
    }))
    render(<QueryClientProvider client={new QueryClient()}><CaptionModel /></QueryClientProvider>)
    const select = await screen.findByLabelText('Live caption model')
    expect(screen.getByText('Runs well on any PC.')).toBeInTheDocument()
    expect(screen.getByText('7.6× real time on the processor: keeps up easily.')).toBeInTheDocument()
    fireEvent.change(select, { target: { value: 'large-v3-turbo' } })
    await waitFor(() => expect(calls.find((c) => c.url === '/api/captions/model' && c.body)?.body).toEqual({ model: 'large-v3-turbo' }))
  })
  it('is not shown when the page cannot be asked', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 500 })))
    const { container } = render(<QueryClientProvider client={new QueryClient()}><CaptionModel /></QueryClientProvider>)
    await waitFor(() => expect(container.querySelector('section')).toBeNull())
  })
})

describe('speedText', () => {
  it('warns when captions cannot keep up', () => {
    expect(speedText(0.6, 'cpu')).toMatchObject({ slow: true })
    expect(speedText(1.8, 'cuda').text).toContain('with little to spare')
    expect(speedText(0, '').slow).toBe(false)
  })
})

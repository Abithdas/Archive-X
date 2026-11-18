import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useRef, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SearchField } from './SearchField'
import type { Suggestion } from './searchSuggest'

const S = (id: number, title: string, kind: 'performers' | 'studios' = 'performers'): Suggestion => ({ id: `${kind}:${id}`, kind, entityId: id, title, description: `${kind === 'performers' ? 'Performer' : 'Studio'} · 3 videos`, image: `/img/${id}` })

function Harness({ initial = '', suggestions = [], onPick = () => {}, onFirstUse }: { initial?: string; suggestions?: Suggestion[]; onPick?: (s: Suggestion) => void; onFirstUse?: () => void }) {
  const [v, setV] = useState(initial)
  const ref = useRef<HTMLInputElement>(null)
  return <SearchField value={v} onChange={setV} placeholder="Search videos" suggestions={v ? suggestions : []} onPick={onPick} onFirstUse={onFirstUse} inputRef={ref} />
}
const box = () => screen.getByRole('combobox', { name: 'Search' }) as HTMLInputElement
const type = (text: string) => fireEvent.change(box(), { target: { value: text } })

beforeEach(() => { vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => setTimeout(() => cb(performance.now()), 0)); vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id)) })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('typing', () => {
  it('draws the typed letters one by one, and only the new ones spring in', () => {
    const { container } = render(<Harness />)
    type('ab')
    let chars = [...container.querySelectorAll('.ax-char')]
    expect(chars.map((c) => c.textContent)).toEqual(['a', 'b'])
    expect(chars.every((c) => c.classList.contains('ax-char-in'))).toBe(true)
    type('abc')
    chars = [...container.querySelectorAll('.ax-char')]
    expect(chars.map((c) => c.classList.contains('ax-char-in'))).toEqual([false, false, true])
  })
  it('keeps the real input in charge (text, keyboard, accessibility) and shows the placeholder only while empty', () => {
    const { container } = render(<Harness />)
    expect(box().getAttribute('aria-autocomplete')).toBe('list')
    expect(container.querySelector('.mo-clear')?.classList.contains('has-value')).toBe(false)
    expect(container.querySelector('.mo-clear-placeholder')?.textContent).toBe('Search videos')
    type('x')
    expect(container.querySelector('.mo-clear')?.classList.contains('has-value')).toBe(true)
    expect(box().value).toBe('x')
  })
  it('has a caret that follows the input', () => {
    const { container } = render(<Harness />)
    expect(container.querySelector('.ax-caret')).not.toBeNull()
  })
  it('tells the page the first time it is used, once', () => {
    const first = vi.fn()
    render(<Harness onFirstUse={first} />)
    fireEvent.focus(box()); fireEvent.blur(box()); fireEvent.focus(box())
    expect(first).toHaveBeenCalledTimes(1)
  })
})

describe('suggestions', () => {
  const list = [S(1, 'Ava Stone'), S(2, 'Avant Studio', 'studios')]
  it('opens a listbox of matches with their pictures and marks the typed letters', () => {
    const { container } = render(<Harness suggestions={list} />)
    fireEvent.focus(box()); type('av')
    const options = screen.getAllByRole('option')
    expect(options).toHaveLength(2)
    expect(options[0]).toHaveTextContent('Ava Stone')
    expect(options[1]).toHaveTextContent('Studio · 3 videos')
    expect(container.querySelector('mark')?.textContent).toBe('Av')
    expect(container.querySelector('img')?.getAttribute('src')).toBe('/img/1')
    expect(box().getAttribute('aria-expanded')).toBe('true')
  })
  it('moves with the arrow keys (wrapping), announces the active one and picks it with Enter', () => {
    const onPick = vi.fn()
    render(<Harness suggestions={list} onPick={onPick} />)
    fireEvent.focus(box()); type('av')
    fireEvent.keyDown(box(), { key: 'ArrowDown' })
    const [a, b] = screen.getAllByRole('option')
    expect(a).toHaveAttribute('aria-selected', 'true')
    expect(box().getAttribute('aria-activedescendant')).toBe(a!.id)
    fireEvent.keyDown(box(), { key: 'ArrowDown' })
    expect(b).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(box(), { key: 'ArrowDown' })
    expect(screen.getAllByRole('option')[0]).toHaveAttribute('aria-selected', 'true')   // wraps round
    fireEvent.keyDown(box(), { key: 'ArrowUp' })
    fireEvent.keyDown(box(), { key: 'Enter' })
    expect(onPick).toHaveBeenCalledWith(list[1])
  })
  it('leaves Enter alone when nothing is highlighted, so the page filter just keeps working', () => {
    const onPick = vi.fn()
    render(<Harness suggestions={list} onPick={onPick} />)
    fireEvent.focus(box()); type('av')
    fireEvent.keyDown(box(), { key: 'Enter' })
    expect(onPick).not.toHaveBeenCalled()
  })
  it('Escape closes the list first and only then clears', async () => {
    render(<Harness suggestions={list} />)
    fireEvent.focus(box()); type('av')
    fireEvent.keyDown(box(), { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('listbox')).toBeNull())   // it leaves with its exit animation
    expect(box().value).toBe('av')
    fireEvent.keyDown(box(), { key: 'Escape' })
    expect(box().value).toBe('')
  })
  it('clicking a result picks it, and a result with a broken picture falls back to its first letter', () => {
    const onPick = vi.fn()
    const { container } = render(<Harness suggestions={list} onPick={onPick} />)
    fireEvent.focus(box()); type('av')
    fireEvent.error(container.querySelector('img')!)
    expect(screen.getAllByRole('option')[0]).toHaveTextContent('A')
    fireEvent.click(screen.getAllByRole('option')[1]!.querySelector('button')!)
    expect(onPick).toHaveBeenCalledWith(list[1])
  })
  it('shows no list when there is nothing to suggest', () => {
    render(<Harness suggestions={[]} />)
    fireEvent.focus(box()); type('zzz')
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(box().getAttribute('aria-expanded')).toBe('false')
  })
})

describe('clearing', () => {
  it('dissolves the text: it clears the value at once, sinks the old text, and tidies up when the animations end', async () => {
    const ends: Array<() => void> = []
    ;(HTMLElement.prototype as unknown as { animate: unknown }).animate = function () {
      let end!: () => void
      const finished = new Promise<void>((r) => { end = r })
      ends.push(end)
      return { finished, cancel: () => undefined }
    }
    try {
      const { container } = render(<Harness initial="hello world" />)
      fireEvent.click(screen.getByRole('button', { name: 'Clear search' }))
      expect(box().value).toBe('')
      const wrap = container.querySelector('.mo-clear')!
      expect(wrap.classList.contains('is-clearing')).toBe(true)
      expect(container.querySelector('.mo-clear-mirror')?.textContent).toBe('hello\u00a0world')
      expect(ends.length).toBe(3)
      await act(async () => { ends.forEach((e) => e()); await Promise.resolve(); await Promise.resolve() })
      expect(wrap.classList.contains('is-clearing')).toBe(false)
      expect(container.querySelector('.mo-clear-mirror')?.textContent).toBe('')
    } finally { delete (HTMLElement.prototype as unknown as { animate?: unknown }).animate }
  })
  it('has no clear button while empty', () => {
    render(<Harness />)
    expect(screen.queryByRole('button', { name: 'Clear search' })).toBeNull()
  })
})

import { act, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useFitGrid } from './fitGrid'

let resize: () => void = () => undefined
class FakeObserver {
  constructor(cb: () => void) { resize = cb }
  observe() {}
  disconnect() {}
}

function Probe({ nominal, seen }: { nominal: number; seen: (v: { cols: number; per: number; style: string }) => void }) {
  const g = useFitGrid(270, 16)
  seen({ cols: g.cols, per: g.fit(nominal), style: String(g.style.gridTemplateColumns) })
  return <div ref={g.ref} />
}

describe('the measured card grid', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('follows the width of the grid and keeps the page size a whole number of rows', () => {
    vi.stubGlobal('ResizeObserver', FakeObserver)
    let width = 1500
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(() => width)
    let last = { cols: 0, per: 0, style: '' }
    render(<Probe nominal={12} seen={(v) => { last = v }} />)
    expect(last).toEqual({ cols: 5, per: 15, style: 'repeat(5, minmax(0, 1fr))' })   // 5 x 270 + 4 x 16 = 1414 fits, 6 would need 1700
    width = 1130
    act(() => resize())
    expect(last).toEqual({ cols: 4, per: 12, style: 'repeat(4, minmax(0, 1fr))' })   // the window got narrower: 12 is now three full rows
    width = 500
    act(() => resize())
    expect(last.cols).toBe(1)
    expect(last.per).toBe(12)
  })
})

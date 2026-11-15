import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { openSelectMenu } from './selectMenu'

beforeAll(() => {
  HTMLElement.prototype.showPopover = function () {}
  HTMLElement.prototype.hidePopover = function () {}
  Element.prototype.scrollIntoView = function () {}
})
beforeEach(() => vi.useFakeTimers())
afterEach(() => { vi.useRealTimers(); document.body.innerHTML = '' })
/** A closed menu folds shut first (about a quarter of a second) and is taken out of the page after that. */
const afterFold = () => vi.advanceTimersByTime(350)

function mount(multiple = false) {
  document.body.innerHTML = '<dialog id="d" open><label>Sort<select aria-label="Sort"><option value="a">Alpha</option><option value="b">Beta</option><option value="c" disabled>Gamma</option></select></label></dialog>'
  const s = document.querySelector('select')!
  s.multiple = multiple
  return s
}
const rows = () => [...document.querySelectorAll<HTMLButtonElement>('[role=listbox] button[role=option]')]

describe('themed select menu', () => {
  it('lists every option, marks the selected one and disables disabled ones', () => {
    const s = mount()
    openSelectMenu(s)
    expect(rows().map((r) => r.textContent)).toEqual(['Alpha', 'Beta', 'Gamma'])
    expect(rows().map((r) => r.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false'])
    expect(rows()[2]!.disabled).toBe(true)
  })
  it('lives inside the modal dialog so it stays clickable', () => {
    const s = mount()
    openSelectMenu(s)
    expect(document.querySelector('dialog [role=listbox]')).not.toBeNull()
  })
  it('choosing an item sets the value, fires change once and closes', () => {
    const s = mount()
    const change = vi.fn()
    s.addEventListener('change', change)
    openSelectMenu(s)
    rows()[1]!.click()
    expect(s.value).toBe('b')
    expect(change).toHaveBeenCalledTimes(1)
    expect(rows()).toHaveLength(3)   // still there while it folds shut
    afterFold()
    expect(rows()).toHaveLength(0)
  })
  it('choosing the current item fires no change', () => {
    const s = mount()
    const change = vi.fn()
    s.addEventListener('change', change)
    openSelectMenu(s)
    rows()[0]!.click()
    expect(change).not.toHaveBeenCalled()
  })
  it('Escape closes without changing', () => {
    const s = mount()
    openSelectMenu(s)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    afterFold()
    expect(rows()).toHaveLength(0)
    expect(s.value).toBe('a')
  })
  it('opening another menu closes the first', () => {
    const s = mount()
    openSelectMenu(s)
    openSelectMenu(s)
    afterFold()
    expect(document.querySelectorAll('[role=listbox]')).toHaveLength(1)
  })
  it('is an accordion menu: a folding panel with the options inside a clipped inner box', () => {
    const s = mount()
    openSelectMenu(s)
    const panel = document.querySelector('[role=listbox]')!
    expect(panel.classList.contains('t-menu')).toBe(true)
    expect(panel.querySelector(':scope > .t-acc-panel-inner')?.querySelectorAll('button[role=option]')).toHaveLength(3)
  })
})

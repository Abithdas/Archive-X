/**
 * Replaces the browser's own drop-down list (which cannot be styled: square rows, no padding, system font) with a menu in the
 * application's look. The native <select> stays in the page as the source of truth, so forms, labels and React state are
 * unchanged: choosing an item sets the select's value and fires its normal `change` event.
 * Multi-selects and list boxes (size > 1) are left alone.
 */
const PANEL = 'fixed m-0 overflow-y-auto rounded-[14px] border border-line-2 bg-surface p-1.5 text-fg shadow-[0_18px_50px_rgb(0_0_0/0.5)]'
const ROW = 'flex w-full cursor-pointer items-center gap-2 rounded-[10px] px-3 py-2 text-left text-sm font-medium hover:bg-fg/5 focus-visible:bg-fg/5 focus-visible:outline-none'
const ON = 'bg-accent/15 text-accent font-semibold'

let open: { close: () => void } | null = null

export function openSelectMenu(select: HTMLSelectElement): void {
  open?.close()
  const options = [...select.options]
  const rect = select.getBoundingClientRect()
  const panel = document.createElement('div')
  panel.setAttribute('popover', 'manual')
  panel.setAttribute('role', 'listbox')
  panel.setAttribute('aria-label', select.getAttribute('aria-label') || select.labels?.[0]?.textContent?.trim() || 'Options')
  panel.className = PANEL
  const rows: HTMLButtonElement[] = []
  options.forEach((o, i) => {
    const b = document.createElement('button')
    b.type = 'button'
    b.setAttribute('role', 'option')
    b.disabled = o.disabled
    b.className = `${ROW} ${o.index === select.selectedIndex ? ON : ''} disabled:opacity-40 disabled:cursor-default`
    b.setAttribute('aria-selected', String(o.index === select.selectedIndex))
    b.textContent = o.textContent
    b.onclick = () => {
      if (select.selectedIndex !== i) {
        // The native setter, so React's value tracking sees the change.
        select.selectedIndex = i
        select.dispatchEvent(new Event('input', { bubbles: true }))
        select.dispatchEvent(new Event('change', { bubbles: true }))
      }
      close()
    }
    rows.push(b)
    panel.append(b)
  })
  // Inside a modal dialog everything outside it is inert, so the menu must live in the dialog to be clickable.
  const host = select.closest('dialog') ?? document.body
  host.append(panel)

  const gap = 6
  const width = Math.max(rect.width, 180)
  panel.style.minWidth = `${Math.min(width, window.innerWidth - 16)}px`
  panel.style.maxWidth = `${window.innerWidth - 16}px`
  panel.showPopover()
  const h = panel.getBoundingClientRect().height
  const below = window.innerHeight - rect.bottom - gap - 8
  const above = rect.top - gap - 8
  const up = h > below && above > below
  const room = Math.max(120, up ? above : below)
  panel.style.maxHeight = `${room}px`
  const used = Math.min(h, room)
  panel.style.top = `${up ? rect.top - gap - used : rect.bottom + gap}px`
  panel.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - panel.offsetWidth - 8))}px`
  panel.style.right = 'auto'
  panel.style.bottom = 'auto'
  ;(rows[select.selectedIndex] ?? rows.find((r) => !r.disabled))?.focus()
  rows[select.selectedIndex]?.scrollIntoView({ block: 'nearest' })

  const onDown = (e: Event) => { if (!panel.contains(e.target as Node)) close() }
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); return }
    const live = rows.filter((r) => !r.disabled)
    const at = live.indexOf(document.activeElement as HTMLButtonElement)
    if (e.key === 'ArrowDown') { e.preventDefault(); live[Math.min(live.length - 1, at + 1)]?.focus() }
    else if (e.key === 'ArrowUp') { e.preventDefault(); live[Math.max(0, at - 1)]?.focus() }
    else if (e.key === 'Home') { e.preventDefault(); live[0]?.focus() }
    else if (e.key === 'End') { e.preventDefault(); live[live.length - 1]?.focus() }
    else if (e.key === 'Tab') close()
  }
  function close() {
    document.removeEventListener('pointerdown', onDown, true)
    document.removeEventListener('keydown', onKey, true)
    window.removeEventListener('resize', close)
    window.removeEventListener('scroll', onScroll, true)
    if (open?.close === close) open = null
    try { panel.hidePopover() } catch { /* already hidden */ }
    panel.remove()
    if (document.activeElement === document.body || panel.contains(document.activeElement)) select.focus({ preventScroll: true })
  }
  const onScroll = (e: Event) => { if (!panel.contains(e.target as Node)) close() }
  document.addEventListener('pointerdown', onDown, true)
  document.addEventListener('keydown', onKey, true)
  window.addEventListener('resize', close)
  window.addEventListener('scroll', onScroll, true)
  open = { close }
}

const eligible = (t: EventTarget | null): t is HTMLSelectElement =>
  t instanceof HTMLSelectElement && !t.multiple && t.size <= 1 && !t.disabled && typeof HTMLElement.prototype.showPopover === 'function'

/** Call once at start-up. */
export function installSelectMenu(): void {
  // pointerdown would also open on touch scroll; click opens on release, and keyboard Enter/Space/Alt+Down open it too.
  document.addEventListener('mousedown', (e) => { if (e.button === 0 && eligible(e.target)) e.preventDefault() }, true)
  document.addEventListener('click', (e) => { if (eligible(e.target)) { e.preventDefault(); openSelectMenu(e.target) } }, true)
  document.addEventListener('keydown', (e) => {
    if (!eligible(e.target)) return
    if (e.key === 'Enter' || e.key === ' ' || (e.altKey && e.key === 'ArrowDown') || e.key === 'F4') { e.preventDefault(); openSelectMenu(e.target) }
  }, true)
}

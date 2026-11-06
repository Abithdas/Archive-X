import { useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { useLibraryStore } from '@/features/library/store'
import { useSearch } from './search'
import { useUi } from './ui'

interface Cmd { id: string; label: string; hint: string; key?: string; run: () => void }

/** Ctrl K palette: the same nine commands as the classic UI, filter as you type, arrows + Enter, Esc closes. */
export function CommandPalette() {
  const open = useUi((s) => s.palette)
  const { setPalette, setNotifications, requestSearchFocus } = useUi.getState()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const ref = useRef<HTMLDialogElement>(null)
  const [q, setQ] = useState('')
  const [i, setI] = useState(0)

  const cmds: Cmd[] = useMemo(() => [
    { id: 'videos', label: 'Browse videos', hint: 'Open the primary media workspace', key: 'G', run: () => void navigate({ to: '/' }) },
    { id: 'search', label: 'Search library', hint: 'Focus the global search field', key: '/', run: () => requestSearchFocus() },
    { id: 'performers', label: 'Open performers', hint: 'Browse people and relationships', run: () => void navigate({ to: '/performers' }) },
    { id: 'studios', label: 'Open studios', hint: 'Browse studio catalogue', run: () => void navigate({ to: '/studios' }) },
    { id: 'ops', label: 'Open Operations', hint: 'Running work, queues and automation', run: () => void navigate({ to: '/operations' }) },
    { id: 'settings', label: 'Open Settings', hint: 'Application configuration', run: () => void navigate({ to: '/settings' }) },
    { id: 'notifications', label: 'Open Notifications', hint: 'Important updates from your library', run: () => setNotifications(true) },
    { id: 'clear', label: 'Clear library filters', hint: 'Reset search and filters', run: () => { useLibraryStore.getState().resetFilters(); useSearch.getState().set('') } },
    { id: 'refresh', label: 'Refresh library', hint: 'Reload the selected channels', run: () => void qc.invalidateQueries({ queryKey: ['library'] }) },
  ], [navigate, qc, requestSearchFocus, setNotifications])

  const shown = cmds.filter((c) => `${c.label} ${c.hint}`.toLowerCase().includes(q.trim().toLowerCase()))
  const idx = Math.min(i, Math.max(0, shown.length - 1))

  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) { d.showModal(); setQ(''); setI(0) }
    if (!open && d.open) d.close()
  }, [open])

  const run = (c?: Cmd) => {
    if (!c) return
    setPalette(false)
    setTimeout(c.run, 0)
  }
  return (
    <dialog
      ref={ref}
      aria-label="Command palette"
      onClose={() => setPalette(false)}
      onClick={(e) => e.target === ref.current && setPalette(false)}
      className="fixed inset-x-0 top-[12vh] m-auto w-[min(640px,calc(100vw-24px))] overflow-hidden rounded-[18px] border border-line-2 bg-surface p-0 text-fg shadow-[0_18px_50px_rgb(0_0_0/0.5)] backdrop:bg-black/55 backdrop:backdrop-blur-sm"
    >
      <div className="flex items-center gap-3 border-b border-line px-4">
        <Icon name="search" className="text-muted" />
        <input
          autoFocus
          value={q}
          onChange={(e) => { setQ(e.target.value); setI(0) }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setI((idx + 1) % Math.max(1, shown.length)) }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setI((idx - 1 + shown.length) % Math.max(1, shown.length)) }
            else if (e.key === 'Enter') { e.preventDefault(); run(shown[idx]) }
          }}
          placeholder="Search commands…"
          aria-label="Search commands"
          role="combobox"
          aria-expanded="true"
          aria-controls="cmd-list"
          aria-activedescendant={shown[idx] ? `cmd-${shown[idx]!.id}` : undefined}
          className="h-16 min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-faint"
        />
        <kbd className="rounded-md border border-line-2 px-2 py-1 text-[11px] font-semibold text-muted">Esc</kbd>
      </div>
      <ul id="cmd-list" role="listbox" className="max-h-[min(60vh,440px)] overflow-y-auto p-2">
        {shown.length ? shown.map((c, n) => (
          <li key={c.id} id={`cmd-${c.id}`} role="option" aria-selected={n === idx}>
            <button type="button" onMouseMove={() => setI(n)} onClick={() => run(c)} className={cn('flex h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-[15px]', n === idx && 'bg-accent/15')}>
              <span className="font-medium">{c.label}</span>
              <span className="truncate text-xs text-faint">{c.hint}</span>
              {c.key ? <kbd className="ml-auto rounded-md border border-line-2 px-1.5 py-0.5 text-[11px] font-semibold text-muted">{c.key}</kbd> : null}
            </button>
          </li>
        )) : <li className="px-3 py-6 text-center text-sm text-muted">No matching commands</li>}
      </ul>
    </dialog>
  )
}

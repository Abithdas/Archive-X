import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/client'
import { entitiesUrl } from '@/api/hooks'
import type { Entity } from '@/api/types'
import { usePhotoStore } from '@/features/photos/store'
import { useNavigate } from '@tanstack/react-router'
import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { useLibraryStore } from '@/features/library/store'
import { useSearch } from './search'
import { useUi } from './ui'

interface Cmd { id: string; label: string; hint: string; key?: string; group: string; run: () => void }
const RECENT_KEY = 'archive-x:palette-recent'
const readRecent = (): string[] => { try { const v = JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]'); return Array.isArray(v) ? v.filter((x) => typeof x === 'string').slice(0, 6) : [] } catch { return [] } }
const writeRecent = (id: string) => { try { localStorage.setItem(RECENT_KEY, JSON.stringify([id, ...readRecent().filter((x) => x !== id)].slice(0, 6))) } catch { /* private mode */ } }

/** Ctrl K palette: pages, actions and, once you type, matching performers and studios; recent choices first; arrows + Enter, Esc closes. */
export function CommandPalette() {
  const open = useUi((s) => s.palette)
  const { setPalette, setNotifications, requestSearchFocus } = useUi.getState()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const ref = useRef<HTMLDialogElement>(null)
  const [q, setQ] = useState('')
  const [i, setI] = useState(0)
  const [recent, setRecent] = useState<string[]>([])
  const term = q.trim().toLowerCase()
  const lookup = open && term.length >= 2
  const performers = useQuery({ queryKey: ['entities', 'performers'] as const, queryFn: () => api<Entity[]>(entitiesUrl('performers')), staleTime: 30_000, enabled: lookup })
  const studios = useQuery({ queryKey: ['entities', 'studios'] as const, queryFn: () => api<Entity[]>(entitiesUrl('studios')), staleTime: 30_000, enabled: lookup })

  const cmds: Cmd[] = useMemo(() => [
    { id: 'photos', group: 'Go to', label: 'Open Photos', hint: 'Browse images and people', run: () => void navigate({ to: '/photos' }) },
    { id: 'people', group: 'Go to', label: 'Open People', hint: 'Faces found in your photos', run: () => { usePhotoStore.getState().setTab('faces'); void navigate({ to: '/photos' }) } },
    { id: 'videos', group: 'Go to', label: 'Browse videos', hint: 'Open the primary media workspace', key: 'G', run: () => void navigate({ to: '/videos' }) },
    { id: 'performers', group: 'Go to', label: 'Open performers', hint: 'Browse everyone linked to your videos', run: () => void navigate({ to: '/performers' }) },
    { id: 'studios', group: 'Go to', label: 'Open studios', hint: 'Browse studio catalogue', run: () => void navigate({ to: '/studios' }) },
    { id: 'ops', group: 'Go to', label: 'Open Operations', hint: 'Running work, queues and automation', run: () => void navigate({ to: '/operations' }) },
    { id: 'transfers', group: 'Go to', label: 'Open Transfers', hint: 'Telegram transfers and metadata', run: () => void navigate({ to: '/transfers' }) },
    { id: 'settings', group: 'Go to', label: 'Open Settings', hint: 'Application configuration', run: () => void navigate({ to: '/settings' }) },
    { id: 'search', group: 'Actions', label: 'Search library', hint: 'Focus the global search field', key: '/', run: () => requestSearchFocus() },
    { id: 'notifications', group: 'Actions', label: 'Open Notifications', hint: 'Important updates from your library', run: () => setNotifications(true) },
    { id: 'clear', group: 'Actions', label: 'Clear library filters', hint: 'Reset search and filters', run: () => { useLibraryStore.getState().resetFilters(); useSearch.getState().set('') } },
    { id: 'refresh', group: 'Actions', label: 'Refresh library', hint: 'Reload the selected channels', run: () => void qc.invalidateQueries({ queryKey: ['library'] }) },
  ], [navigate, qc, requestSearchFocus, setNotifications])

  const entityCmd = (kind: 'performers' | 'studios', e: Entity): Cmd => ({ id: `${kind}:${e.id}:${e.name}`, group: kind === 'performers' ? 'Performers' : 'Studios', label: e.name, hint: `${e.video_count} video${e.video_count === 1 ? '' : 's'}`, run: () => void navigate({ to: kind === 'performers' ? '/performers/$id' : '/studios/$id', params: { id: String(e.id) } }) })
  const shown: Cmd[] = useMemo(() => {
    if (!term) {
      const byId = new Map(cmds.map((c) => [c.id, c]))
      const recents: Cmd[] = recent.flatMap((id) => {
        const c = byId.get(id)
        if (c) return [{ ...c, group: 'Recent' }]
        const m = /^(performers|studios):(\d+):(.*)$/.exec(id)
        return m ? [{ ...entityCmd(m[1] as 'performers' | 'studios', { id: Number(m[2]), name: m[3]!, video_count: 0 } as Entity), id, group: 'Recent', hint: m[1] === 'performers' ? 'Performer' : 'Studio' }] : []
      }).slice(0, 4)
      return [...recents, ...cmds]
    }
    const match = (c: Cmd) => `${c.label} ${c.hint}`.toLowerCase().includes(term)
    const pick = (kind: 'performers' | 'studios', list?: Entity[]) => (list ?? []).filter((e) => e.name.toLowerCase().includes(term)).sort((a, b) => Number(b.name.toLowerCase().startsWith(term)) - Number(a.name.toLowerCase().startsWith(term)) || b.video_count - a.video_count).slice(0, 5).map((e) => entityCmd(kind, e))
    return [...cmds.filter(match), ...pick('performers', performers.data), ...pick('studios', studios.data)]
  }, [cmds, recent, term, performers.data, studios.data]) // eslint-disable-line react-hooks/exhaustive-deps
  const idx = Math.min(i, Math.max(0, shown.length - 1))

  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) { d.showModal(); setQ(''); setI(0); setRecent(readRecent()) }
    if (!open && d.open) d.close()
  }, [open])

  const run = (c?: Cmd) => {
    if (!c) return
    writeRecent(c.id)
    setPalette(false)
    setTimeout(c.run, 0)
  }
  return (
    <dialog
      ref={ref}
      aria-label="Command palette"
      onClose={() => setPalette(false)}
      onClick={(e) => e.target === ref.current && setPalette(false)}
      className="fixed inset-x-0 top-[calc(12vh/(var(--ax-ui-zoom,1)*var(--ax-page-zoom,1)))] m-auto w-[min(640px,calc((100vw-24px)/(var(--ax-ui-zoom,1)*var(--ax-page-zoom,1))))] overflow-hidden rounded-[18px] border border-line-2 bg-surface p-0 text-fg shadow-[0_18px_50px_rgb(0_0_0/0.5)] backdrop:bg-black/55 backdrop:backdrop-blur-sm"
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
          placeholder="Search pages, actions, performers, studios…"
          aria-label="Search commands"
          role="combobox"
          aria-expanded="true"
          aria-controls="cmd-list"
          aria-activedescendant={shown[idx] ? `cmd-${idx}` : undefined}
          className="h-16 min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-faint"
        />
        <kbd className="rounded-md border border-line-2 px-2 py-1 text-[11px] font-semibold text-muted">Esc</kbd>
      </div>
      <ul id="cmd-list" role="listbox" className="max-h-[min(calc(60vh/(var(--ax-ui-zoom,1)*var(--ax-page-zoom,1))),440px)] overflow-y-auto p-2">
        {shown.length ? shown.map((c, n) => (
          <Fragment key={`${c.group}:${c.id}`}>
          {n === 0 || shown[n - 1]!.group !== c.group ? <li role="presentation" className="px-3 pb-1 pt-2 text-[11px] font-bold uppercase tracking-wider text-faint">{c.group}</li> : null}
          <li id={`cmd-${n}`} role="option" aria-selected={n === idx}>
            <button type="button" onMouseMove={() => setI(n)} onClick={() => run(c)} className={cn('flex h-11 w-full items-center gap-3 rounded-xl px-3 text-left text-[15px]', n === idx && 'bg-accent/15')}>
              <span className="font-medium">{c.label}</span>
              <span className="truncate text-xs text-faint">{c.hint}</span>
              {c.key ? <kbd className="ml-auto rounded-md border border-line-2 px-1.5 py-0.5 text-[11px] font-semibold text-muted">{c.key}</kbd> : null}
            </button>
          </li>
          </Fragment>
        )) : <li className="px-3 py-6 text-center text-sm text-muted">No matching commands</li>}
      </ul>
    </dialog>
  )
}

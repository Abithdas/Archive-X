import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { useEntities } from '@/api/hooks'
import type { EntityKind } from '@/api/types'
import { Button } from '@/design/Button'
import { Confirm } from '@/design/Confirm'
import { PageHead } from '@/design/PageHead'
import { Segmented } from '@/design/Segmented'
import { useSearch } from '@/app/search'
import { SkeletonGroup } from '@/design/Skeleton'
import { EntityCard, EntityCardSkeleton } from './EntityCard'
import { useAutoRepair } from './autoRepair'
import { useFitGrid } from '@/features/library/fitGrid'
import { Pagination } from '@/features/library/Pagination'
import { filterEntities, pageCount, type GenderFilter } from './filter'
import { FAILED, IDLE, NEED_SELECTION, SORTS, canCommit, commitLabel, confirmation, duplicatesOf, hint, mergeBody, modeLabel, pick, sortBy, successMessage, toggleMode, type MergeState, type SortKey } from './merge'
import { Empty } from '@/design/Empty'
import { useDirectoryView } from './directoryView'
import { SpinCounter } from '@/design/SpinCounter'

const GRID = 'grid gap-[18px]'
const COPY = {
  performers: { eyebrow: 'Library', title: 'Performers', blurb: 'Everyone linked to your videos', empty: 'No performers yet.' },
  studios: { eyebrow: 'Library', title: 'Studios', blurb: 'Studios and sites in your archive', empty: 'No studios yet.' },
} as const

export function EntityDirectory({ kind }: { kind: EntityKind }) {
  const copy = COPY[kind]
  const entities = useEntities(kind)
  useAutoRepair(kind)
  const query = useSearch((s) => s.q)
  const { page, gender, per: nominalPer, sort } = useDirectoryView((s) => s.views[kind])
  const patch = useDirectoryView((s) => s.patch)
  const setGender = (g: GenderFilter) => patch(kind, { gender: g, page: 1 })
  const setPage = (n: number) => patch(kind, { page: n })
  const setPer = (n: number) => patch(kind, { per: n })
  const setSort = (v: SortKey) => patch(kind, { sort: v })
  const grid = useFitGrid(typeof window !== 'undefined' && window.innerWidth < 640 ? 150 : 190, 18)
  const per = grid.fit(nominalPer)   // whole rows of whatever the window shows, so the last row is full
  const [merge, setMerge] = useState<MergeState>(IDLE)
  const [asking, setAsking] = useState(false)
  const qc = useQueryClient()
  const rows = useMemo(() => sortBy(filterEntities(entities.data ?? [], query, kind === 'performers' ? gender : 'all'), sort), [entities.data, query, gender, kind, sort])
  const pages = pageCount(rows.length, per)
  const current = Math.min(page, pages)
  const lastQuery = useRef(query)
  useEffect(() => { if (lastQuery.current !== query) { lastQuery.current = query; patch(kind, { page: 1 }) } }, [query, kind, patch])   // a new search starts at page 1; coming back to the list does not
  useEffect(() => setMerge(IDLE), [kind])   // merge mode belongs to the performers list
  const visible = rows.slice((current - 1) * per, current * per)
  const all = entities.data ?? []
  const merging = kind === 'performers' && merge.active
  const say = (m: string) => m && toast(m)
  const toggle = () => { const r = toggleMode(merge); setMerge(r.state); say(r.message) }
  const pickCard = (id: number) => { const r = pick(merge, id, all); setMerge(r.state); say(r.message) }
  const keep = all.find((p) => p.id === merge.keepId)
  const ids = duplicatesOf(merge)
  const commit = async () => {
    if (!keep || !ids.length) return void toast(NEED_SELECTION)
    setAsking(false)
    setMerge((m) => ({ ...m, busy: true }))
    try {
      const r = await api<{ ok?: boolean; merged_count?: number; name?: string; detail?: string }>('/api/performers/merge-batch', { method: 'POST', json: mergeBody(keep.id, ids) })
      if (!r?.ok) throw new ApiError(0, r?.detail || FAILED)
      setMerge(IDLE)
      await qc.invalidateQueries({ queryKey: ['entities'] })
      toast(successMessage(r, ids.length, keep.name))
    } catch (e) {
      setMerge((m) => ({ ...m, busy: false }))
      toast.error(e instanceof ApiError && e.detail ? e.detail : FAILED)
    }
  }

  return (
    <section className="mx-auto flex w-full max-w-[1800px] flex-1 flex-col pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10">
      <PageHead title={copy.title} blurb={copy.blurb} />
      <div className="mb-5 flex flex-wrap items-center gap-2.5">
        {kind === 'performers' ? (
          <Segmented
            label="Gender"
            value={gender}
            onChange={setGender}
            options={[{ value: 'female', label: 'Female' }, { value: 'male', label: 'Male' }, { value: 'all', label: 'All' }]}
          />
        ) : null}
        {kind === 'performers' ? (
          <>
            <label className="inline-flex items-center gap-2 text-sm text-muted">Sort
              <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} aria-label="Sort performers" className="h-10 rounded-xl border border-line-2 bg-surface px-3 text-sm font-semibold text-fg">
                {SORTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
            <Button variant={merge.active ? 'primary' : 'outline'} disabled={merge.busy} onClick={toggle}>{modeLabel(merge.active)}</Button>
            {merge.active ? <Button variant="primary" disabled={!canCommit(merge, all)} onClick={() => setAsking(true)}>{commitLabel(merge.mergeIds.length)}</Button> : null}
            {merge.active ? <span role="status" aria-live="polite" className="max-w-[480px] text-xs text-muted">{hint(merge, all)}</span> : null}
          </>
        ) : null}
        {entities.data ? <span className="ml-auto text-sm text-muted"><SpinCounter value={rows.length} /> {copy.title.toLowerCase()}</span> : null}
      </div>

      {entities.isPending ? (
        <SkeletonGroup label={`Loading ${copy.title.toLowerCase()}`} className={GRID} ref={grid.ref} style={grid.style}>
          {Array.from({ length: grid.cols * 2 }, (_, i) => <EntityCardSkeleton key={i} kind={kind} />)}
        </SkeletonGroup>
      ) : entities.isError ? (
        <div role="alert" className="grid place-items-center gap-3 rounded-card border border-line py-16 text-center">
          <p className="font-semibold">Could not load {copy.title.toLowerCase()}.</p>
          <Button variant="primary" onClick={() => void entities.refetch()}>Retry</Button>
        </div>
      ) : !rows.length ? (
        <Empty icon={kind === 'performers' ? 'user' : 'studio'}>
          {entities.data.length ? (kind === 'performers' && gender !== 'all' && !query.trim() ? `No ${gender} performers. ` : 'No matches. ') : copy.empty}
          {entities.data.length && kind === 'performers' && gender !== 'all' ? <button type="button" onClick={() => setGender('all')} className="font-semibold text-accent underline">Show all performers</button> : null}
        </Empty>
      ) : (
        <>
          <div className={GRID} ref={grid.ref} style={grid.style}>{visible.map((e, i) => <EntityCard key={e.id} index={i} kind={kind} entity={e} merge={merging ? { keeper: merge.keepId === e.id, selected: merge.mergeIds.includes(e.id), onPick: () => pickCard(e.id) } : undefined} />)}</div>
          <Pagination page={current - 1} pages={pages} per={nominalPer} fit={grid.fit} onPage={(n) => setPage(n + 1)} onPer={(n) => { setPer(n); setPage(1) }} />
        </>
      )}
      {asking && keep ? (() => { const c = confirmation(keep, ids, all); return <Confirm title={c.title} text={c.text} ok={c.ok} danger onCancel={() => setAsking(false)} onOk={() => void commit()} /> })() : null}
    </section>
  )
}

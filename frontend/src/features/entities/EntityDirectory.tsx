import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
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
import { EntityCard, EntityCardSkeleton, StudioTile, StudioTileSkeleton } from './EntityCard'
import { Pagination } from '@/features/library/Pagination'
import { filterEntities, pageCount, type GenderFilter } from './filter'
import { FAILED, IDLE, NEED_SELECTION, SORTS, canCommit, commitLabel, confirmation, duplicatesOf, hint, mergeBody, modeLabel, pick, sortBy, successMessage, toggleMode, type MergeState, type SortKey } from './merge'

const GRID = 'grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-[18px] sm:grid-cols-[repeat(auto-fill,minmax(190px,1fr))]'
const STUDIO_GRID = 'grid grid-cols-1 gap-[18px] sm:grid-cols-[repeat(auto-fill,minmax(260px,1fr))]'
const DEFAULT_PER = 12
const COPY = {
  performers: { eyebrow: 'Library', title: 'Performers', blurb: 'Everyone linked to your videos', empty: 'No performers yet.' },
  studios: { eyebrow: 'Library', title: 'Studios', blurb: 'Studios and sites in your archive', empty: 'No studios yet.' },
} as const

export function EntityDirectory({ kind }: { kind: EntityKind }) {
  const copy = COPY[kind]
  const entities = useEntities(kind)
  const query = useSearch((s) => s.q)
  const [gender, setGender] = useState<GenderFilter>('all')
  const [page, setPage] = useState(1)
  const [per, setPer] = useState(DEFAULT_PER)
  const [sort, setSort] = useState<SortKey>('default')
  const [merge, setMerge] = useState<MergeState>(IDLE)
  const [asking, setAsking] = useState(false)
  const qc = useQueryClient()
  const rows = useMemo(() => sortBy(filterEntities(entities.data ?? [], query, kind === 'performers' ? gender : 'all'), sort), [entities.data, query, gender, kind, sort])
  const pages = pageCount(rows.length, per)
  const current = Math.min(page, pages)
  useEffect(() => setPage(1), [query, gender, kind])
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
    <section className="mx-auto w-full max-w-[1800px] pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10">
      <PageHead title={copy.title} blurb={copy.blurb} />
      <div className="mb-5 flex flex-wrap items-center gap-2.5">
        {kind === 'performers' ? (
          <Segmented
            label="Gender"
            value={gender}
            onChange={setGender}
            options={[{ value: 'all', label: 'All' }, { value: 'female', label: 'Female' }, { value: 'male', label: 'Male' }]}
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
        {entities.data ? <span className="ml-auto text-sm text-muted">{rows.length} {copy.title.toLowerCase()}</span> : null}
      </div>

      {entities.isPending ? (
        <SkeletonGroup label={`Loading ${copy.title.toLowerCase()}`} className={kind === 'studios' ? STUDIO_GRID : GRID}>
          {Array.from({ length: 12 }, (_, i) => kind === 'studios' ? <StudioTileSkeleton key={i} /> : <EntityCardSkeleton key={i} />)}
        </SkeletonGroup>
      ) : entities.isError ? (
        <div role="alert" className="grid place-items-center gap-3 rounded-card border border-line py-16 text-center">
          <p className="font-semibold">Could not load {copy.title.toLowerCase()}.</p>
          <Button variant="primary" onClick={() => void entities.refetch()}>Retry</Button>
        </div>
      ) : !rows.length ? (
        <p className="rounded-card border border-dashed border-line py-16 text-center text-muted">
          {entities.data.length ? 'No matches.' : copy.empty}
        </p>
      ) : (
        <>
          {kind === 'studios'
            ? <div className={STUDIO_GRID}>{visible.map((e) => <StudioTile key={e.id} entity={e} />)}</div>
            : <div className={GRID}>{visible.map((e) => <EntityCard key={e.id} kind={kind} entity={e} merge={merging ? { keeper: merge.keepId === e.id, selected: merge.mergeIds.includes(e.id), onPick: () => pickCard(e.id) } : undefined} />)}</div>}
          <Pagination page={current - 1} pages={pages} per={per} onPage={(n) => setPage(n + 1)} onPer={(n) => { setPer(n); setPage(1) }} />
        </>
      )}
      {asking && keep ? (() => { const c = confirmation(keep, ids, all); return <Confirm title={c.title} text={c.text} ok={c.ok} danger onCancel={() => setAsking(false)} onOk={() => void commit()} /> })() : null}
    </section>
  )
}

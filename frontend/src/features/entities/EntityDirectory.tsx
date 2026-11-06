import { useEffect, useMemo, useState } from 'react'
import { useEntities } from '@/api/hooks'
import type { EntityKind } from '@/api/types'
import { Button } from '@/design/Button'
import { PageHead } from '@/design/PageHead'
import { Segmented } from '@/design/Segmented'
import { useSearch } from '@/app/search'
import { SkeletonGroup } from '@/design/Skeleton'
import { EntityCard, EntityCardSkeleton } from './EntityCard'
import { filterEntities, pageCount, type GenderFilter } from './filter'

const PER = 48
const GRID = 'grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-[18px] sm:grid-cols-[repeat(auto-fill,minmax(190px,1fr))]'
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
  const rows = useMemo(() => filterEntities(entities.data ?? [], query, kind === 'performers' ? gender : 'all'), [entities.data, query, gender, kind])
  const pages = pageCount(rows.length, PER)
  const current = Math.min(page, pages)
  useEffect(() => setPage(1), [query, gender, kind])
  const visible = rows.slice((current - 1) * PER, current * PER)

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
        {entities.data ? <span className="ml-auto text-sm text-muted">{rows.length} {copy.title.toLowerCase()}</span> : null}
      </div>

      {entities.isPending ? (
        <SkeletonGroup label={`Loading ${copy.title.toLowerCase()}`} className={GRID}>
          {Array.from({ length: 16 }, (_, i) => <EntityCardSkeleton key={i} />)}
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
          <div className={GRID}>{visible.map((e) => <EntityCard key={e.id} kind={kind} entity={e} />)}</div>
          {pages > 1 ? (
            <nav aria-label="Pages" className="mt-6 flex items-center justify-center gap-2">
              <Button variant="outline" disabled={current <= 1} onClick={() => setPage(current - 1)}>Previous</Button>
              <span className="text-sm text-muted">Page {current} of {pages}</span>
              <Button variant="outline" disabled={current >= pages} onClick={() => setPage(current + 1)}>Next</Button>
            </nav>
          ) : null}
        </>
      )}
    </section>
  )
}

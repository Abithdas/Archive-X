import { useEffect, useMemo, useState } from 'react'
import { useEntities } from '@/api/hooks'
import type { EntityKind } from '@/api/types'
import { Button } from '@/design/Button'
import { SkeletonGroup } from '@/design/Skeleton'
import { EntityCard, EntityCardSkeleton } from './EntityCard'
import { filterEntities, pageCount, type GenderFilter } from './filter'

const PER = 48
const GRID = 'grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6 2xl:grid-cols-8'
const COPY = {
  performers: { eyebrow: 'Library', title: 'Performers', blurb: 'Everyone in your archive', empty: 'No performers yet.' },
  studios: { eyebrow: 'Library', title: 'Studios', blurb: 'Studios and sites in your archive', empty: 'No studios yet.' },
} as const

export function EntityDirectory({ kind }: { kind: EntityKind }) {
  const copy = COPY[kind]
  const entities = useEntities(kind)
  const [query, setQuery] = useState('')
  const [gender, setGender] = useState<GenderFilter>('all')
  const [page, setPage] = useState(1)
  const rows = useMemo(() => filterEntities(entities.data ?? [], query, kind === 'performers' ? gender : 'all'), [entities.data, query, gender, kind])
  const pages = pageCount(rows.length, PER)
  const current = Math.min(page, pages)
  useEffect(() => setPage(1), [query, gender, kind])
  const visible = rows.slice((current - 1) * PER, current * PER)

  return (
    <section className="mx-auto w-full max-w-[1800px] p-4 sm:p-6">
      <header className="mb-4">
        <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-accent">{copy.eyebrow}</p>
        <h1 className="text-3xl font-extrabold tracking-tight">{copy.title}</h1>
        <p className="text-sm text-muted">{copy.blurb}</p>
      </header>

      <div className="sticky top-0 z-10 -mx-4 mb-4 flex flex-wrap items-center gap-2 bg-bg/90 px-4 py-2 backdrop-blur sm:-mx-6 sm:px-6">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={`Search ${copy.title.toLowerCase()}`}
          aria-label={`Search ${copy.title.toLowerCase()}`}
          className="h-10 min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 text-sm sm:max-w-sm"
        />
        {kind === 'performers' ? (
          <div role="group" aria-label="Gender" className="flex gap-1">
            {(['all', 'female', 'male'] as const).map((g) => (
              <Button key={g} variant={gender === g ? 'primary' : 'outline'} aria-pressed={gender === g} onClick={() => setGender(g)}>
                {g === 'all' ? 'All' : g === 'female' ? 'Female' : 'Male'}
              </Button>
            ))}
          </div>
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

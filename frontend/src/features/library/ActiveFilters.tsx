import { Icon } from '@/design/Icon'
import { RESOLUTIONS } from './FiltersDrawer'
import { useLibraryStore, type Filters } from './store'

const LABELS: Record<keyof Filters, string> = { studio: 'Studio', performer: 'Performer', tag: 'Tag', dateFrom: 'From', dateTo: 'To', resolution: 'Resolution' }

/** One removable chip per applied filter (the classic "active filters" strip under the summary line). */
export function ActiveFilters() {
  const filters = useLibraryStore((s) => s.filters)
  const remove = useLibraryStore((s) => s.removeFilter)
  const entries = (Object.keys(LABELS) as (keyof Filters)[]).filter((k) => filters[k])
  if (!entries.length) return null
  return (
    <ul className="mb-4 flex flex-wrap gap-2" aria-label="Active filters">
      {entries.map((k) => (
        <li key={k} className="inline-flex items-center gap-1.5 rounded-full bg-accent/15 py-1 pl-3 pr-1.5 text-[13px] font-semibold text-accent">
          {LABELS[k]}: {k === 'resolution' ? (RESOLUTIONS.find((r) => r.value === filters[k])?.label ?? filters[k]) : filters[k]}
          <button type="button" onClick={() => remove(k)} aria-label={`Remove ${LABELS[k]} filter`} className="grid size-5 place-items-center rounded-full hover:bg-accent/20">
            <Icon name="close" className="size-3" />
          </button>
        </li>
      ))}
    </ul>
  )
}

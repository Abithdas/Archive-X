import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { Popover } from '@/features/photos/Popover'
import { filterCount, optionsOf, type Facets, type ListFilter, NO_FILTER } from './listFilters'

const OPTION = 'h-9 rounded-full border px-3.5 text-[13px] font-semibold transition-colors'
const pick = (on: boolean) => cn(OPTION, on ? 'border-accent/60 bg-accent/15 text-fg' : 'border-line text-muted hover:bg-fg/5 hover:text-fg')

function Group({ title, value, options, onPick }: { title: string; value: string; options: ReadonlyArray<readonly [string, string]>; onPick: (v: string) => void }) {
  if (!options.length) return null
  return (
    <div className="grid gap-2">
      <h3 className="text-sm font-bold">{title}</h3>
      <div role="group" aria-label={title} className="flex flex-wrap gap-1.5">
        {[['', 'Any'] as const, ...options].map(([v, label]) => <button key={v || 'any'} type="button" aria-pressed={value === v} onClick={() => onPick(v)} className={pick(value === v)}>{label}</button>)}
      </div>
    </div>
  )
}

/**
 * One Filters button for a list of movies or shows, like the Photos page: sort order, quality, source, HDR or Dolby Vision and language, with a count of
 * what is chosen. Only the choices that exist in the library are offered.
 */
export function ListFilters({ items, value, onChange, sort }: { items: ReadonlyArray<Partial<Facets>>; value: ListFilter; onChange: (f: ListFilter) => void; sort?: { value: string; options: ReadonlyArray<{ value: string; label: string }>; onChange: (v: string) => void } }) {
  const o = optionsOf(items)
  const count = filterCount(value)
  const set = (patch: Partial<ListFilter>) => onChange({ ...value, ...patch })
  return (
    <Popover label="Filters" icon={<Icon name="filter" className="size-4" />} badge={count} active={count > 0} align="right">
      <div className="grid gap-4">
        {sort ? <Group title="Sort" value={sort.value} options={sort.options.map((s) => [s.value, s.label] as const)} onPick={sort.onChange} /> : null}
        <Group title="Quality" value={value.quality} options={o.qualities} onPick={(quality) => set({ quality })} />
        <Group title="Source" value={value.source} options={o.sources.map((s) => [s, s] as const)} onPick={(source) => set({ source })} />
        <Group title="HDR / DV" value={value.hdr} options={o.hdrs} onPick={(hdr) => set({ hdr })} />
        <Group title="Language" value={value.lang} options={o.langs.map((l) => [l, l] as const)} onPick={(lang) => set({ lang })} />
        {o.langs.length ? <p className="text-xs text-muted">Languages come from tags in the file names.</p> : null}
        {count ? <button type="button" onClick={() => onChange(NO_FILTER)} className="justify-self-start text-[13px] font-semibold text-muted hover:text-fg">Clear filters</button> : null}
      </div>
    </Popover>
  )
}

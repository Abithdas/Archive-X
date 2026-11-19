import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

interface Group { key: string; label: string; providers: Array<{ name: string; logo: string }> }
interface Where { ok?: boolean; needs_key?: boolean; region?: string; groups?: Group[]; link?: string }

/** The country the person is in, as far as the browser says (en-GB is GB); the United States when it does not say. */
export const regionOf = (language = typeof navigator === 'undefined' ? '' : navigator.language): string => (/-([A-Za-z]{2})\b/.exec(language)?.[1] ?? 'US').toUpperCase()

/**
 * Where a title you do not have can be watched legitimately: the services that offer it in your country, and a link to the list (TMDB's page about it).
 * Nothing plays here; you open the service. The data comes from JustWatch through TMDB, which asks for the credit shown below.
 */
export function WhereToWatch({ kind, ident }: { kind: 'movie' | 'tv'; ident: string }) {
  const region = regionOf()
  const q = useQuery({ queryKey: ['where-to-watch', kind, ident, region] as const, queryFn: () => api<Where>(`/api/screen/where/${kind === 'tv' ? 'series' : 'movie'}/${ident}?region=${region}`), staleTime: 30 * 60_000, retry: false })
  if (q.isError || !q.data) return null
  const d = q.data
  return (
    <section aria-label="Where to watch" className="grid gap-2">
      <h3 className="text-sm font-bold">Where to watch</h3>
      {d.needs_key ? <p className="text-xs text-muted">Add a free TMDB key in Settings, Metadata to see where to watch.</p>
        : !d.groups?.length ? <p className="text-xs text-muted">Not offered by a streaming service in {d.region} right now.</p>
        : d.groups.map((g) => (
          <div key={g.key} className="flex flex-wrap items-center gap-1.5">
            <span className="w-24 shrink-0 text-[11px] font-bold uppercase tracking-wide text-faint">{g.label}</span>
            <ul aria-label={g.label} className="m-0 flex list-none flex-wrap gap-1.5 p-0">
              {g.providers.map((p) => <li key={p.name} className="inline-flex items-center gap-1.5 rounded-full bg-fg/10 py-1 pl-1 pr-3 text-[13px] font-semibold">{p.logo ? <img src={p.logo} alt="" loading="lazy" className="size-5 rounded-full object-cover" /> : null}{p.name}</li>)}
            </ul>
          </div>
        ))}
      {d.link ? <a href={d.link} target="_blank" rel="noreferrer" className="w-fit text-[13px] font-semibold text-accent hover:underline">See every option for {d.region} →</a> : null}
      <p className="text-[11px] text-faint">Streaming data by <a href="https://www.justwatch.com" target="_blank" rel="noreferrer" className="underline">JustWatch</a>, through TMDB.</p>
    </section>
  )
}

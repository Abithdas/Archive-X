import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { api } from '@/api/client'
import { Icon } from '@/design/Icon'
import { PLACEHOLDER, PosterCard } from './PosterCard'
import { OtherCreditCards, type CreditsAnswer } from './OtherCredits'

interface LibraryTitle { id: number; kind: 'movie' | 'tv'; title: string; year: number | null; poster: string }
interface CastAnswer extends CreditsAnswer { name: string; image: string; library: LibraryTitle[] }

/** What one cast member is in: the movies and shows of the library first, then their other movies and shows (each opens its own page). */
export function CastPage({ name }: { name: string }) {
  const q = useQuery({ queryKey: ['cast', name] as const, queryFn: () => api<CastAnswer>(`/api/screen/cast/${encodeURIComponent(name)}`), staleTime: 10 * 60_000, retry: false })
  const d = q.data
  return (
    <section aria-label={`Everything with ${name}`} className="mx-auto w-full max-w-[calc(1800px/var(--ax-ui-zoom,1))] px-4 pb-12 pt-6 sm:px-10">
      <button type="button" onClick={() => window.history.back()} aria-label="Go back" title="Go back" className="grid size-10 place-items-center rounded-full bg-fg/10 hover:bg-fg/20"><Icon name="arrow-left" className="size-5" /></button>
      <div className="mt-4 flex items-center gap-4">
        <img src={d?.image || PLACEHOLDER} alt="" className="size-20 rounded-full bg-surface-2 object-cover" />
        <h1 className="m-0 text-[clamp(28px,4vw,48px)] font-black leading-none tracking-tight">{name}</h1>
      </div>
      {q.isPending ? <p role="status" className="mt-8 text-sm text-muted">Looking up {name}…</p> : q.isError ? <p role="alert" className="mt-8 text-sm text-warn">Could not load this person.</p> : (
        <>
          {d!.library.length ? (
            <section aria-label="In your library" className="mt-8">
              <h2 className="mb-3 text-lg font-extrabold">In your library</h2>
              <ul className="m-0 grid list-none grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-4 p-0">
                {d!.library.map((t) => (
                  <li key={`${t.kind}${t.id}`} className="m-0"><PosterCard to={t.kind === 'tv' ? '/series/$id' : '/movies/$id'} params={{ id: String(t.id) }} image={t.poster} title={t.title} sub={t.year ? String(t.year) : ''} badge={t.kind === 'tv' ? 'Series' : undefined} /></li>
                ))}
              </ul>
            </section>
          ) : null}
          <OtherCreditCards name={name} data={d!} />
          {!d!.library.length && !d!.items.length && !d!.reason ? <p className="mt-8 text-sm text-muted">Nothing found for {name}.</p> : null}
          <Link to="/videos" className="mt-8 inline-block text-sm font-semibold text-muted hover:text-fg">Back to movies and shows</Link>
        </>
      )}
    </section>
  )
}

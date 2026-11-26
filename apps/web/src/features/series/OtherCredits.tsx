import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { PosterCard } from './PosterCard'

interface Credit { imdb: string; kind: 'movie' | 'tv'; title: string; year: number | null; poster: string; rating: string; character?: string }
export interface CreditsAnswer { items: Credit[]; reason: string }

const WHY: Record<string, string> = {
  tmdb: 'Add a TMDB key in Settings, Metadata to see the other movies and shows this person is in.',
  unlinked: 'This person is not linked to a TMDB profile yet, so their other work cannot be listed.',
  unreachable: 'Could not reach TMDB just now. Try again later.',
}

/** The cards (or the reason there are none) of an actor's other movies and shows. */
export function OtherCreditCards({ name, data }: { name: string; data: CreditsAnswer }) {
  const items = data.items
  const why = WHY[data.reason ?? '']
  if (!items.length && !why) return null
  return (
    <section aria-label={`More with ${name}`} className="mt-10">
      <h2 className="mb-1 text-lg font-extrabold">More with {name}</h2>
      <p className="mb-4 text-sm text-muted">Movies and shows they are in that are not in your library.</p>
      {items.length ? (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-4">
          {items.map((c) => (
            <PosterCard key={`${c.kind}:${c.imdb}`} to="/discover/$kind/$id" params={{ kind: c.kind === 'tv' ? 'series' : 'movie', id: c.imdb }} image={c.poster} title={c.title}
              sub={[c.year, c.character].filter(Boolean).join(' · ')} rating={c.rating} badge={c.kind === 'tv' ? 'Series' : undefined} />
          ))}
        </div>
      ) : <p className="text-sm text-muted">{why}</p>}
    </section>
  )
}

/** On an actor's page of the library, below its own movies and shows. */
export function OtherCredits({ id, name }: { id: number; name: string }) {
  const q = useQuery({ queryKey: ['person-credits', id] as const, queryFn: () => api<CreditsAnswer>(`/api/screen/person-credits/${id}`), staleTime: 10 * 60_000 })
  if (q.isPending) return <p role="status" className="mt-8 text-sm text-muted">Looking up the other work of {name}…</p>
  return <OtherCreditCards name={name} data={q.data ?? { items: [], reason: '' }} />
}

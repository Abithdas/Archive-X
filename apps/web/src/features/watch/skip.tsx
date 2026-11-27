import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { cn } from '@/design/cn'

/** A stretch of a movie or episode that can be skipped: the opening, a recap of the last episode, the end credits, or a preview of the next one. `end` is null when it runs to the end. */
export interface Skip { type: 'intro' | 'recap' | 'credits' | 'preview'; start: number; end: number | null }

const LABELS: Record<Skip['type'], string> = { intro: 'Skip intro', recap: 'Skip recap', credits: 'Skip credits', preview: 'Skip preview' }
export const skipLabel = (s: Skip, canNext: boolean): string => (s.type === 'credits' && canNext ? 'Next episode' : LABELS[s.type])

/** The stretch to offer skipping at this moment, or null (nothing in the last second of a stretch, so the button does not appear as it ends). */
export function skipNow(skips: readonly Skip[], time: number, duration: number): Skip | null {
  const end = duration > 0 ? duration : Infinity
  for (const s of skips) {
    if (time >= s.start && time < (s.end ?? end) - 1) return s
  }
  return null
}

/** Where the skip lands: the end of the stretch (past the last second when it runs to the end of the video). */
export const skipTarget = (s: Skip, duration: number): number => s.end ?? duration

/** The times for a video or an add-on title, from the server (community databases). An answer of none, or a failure, is simply no button. */
export function useSkipTimes(path: string | null): Skip[] {
  const q = useQuery({ queryKey: ['skip-times', path] as const, enabled: Boolean(path), staleTime: 3_600_000, retry: false, queryFn: () => api<{ segments: Skip[] }>(path!).then((d) => d.segments ?? []) })
  return q.data ?? []
}

/** The Skip button: bottom right of the picture (above the controls), shown while a skippable stretch is playing. */
export function SkipButton({ skip, canNext, onPress, className }: { skip: Skip | null; canNext: boolean; onPress: () => void; className?: string }) {
  if (!skip) return null
  return (
    <button type="button" data-testid="skip-button" onClick={onPress}
      className={cn('z-30 inline-flex h-11 items-center gap-2 rounded-lg border border-white/70 bg-black/65 px-5 text-sm font-bold text-white backdrop-blur hover:bg-white hover:text-black', className)}>
      {skipLabel(skip, canNext)}
    </button>
  )
}

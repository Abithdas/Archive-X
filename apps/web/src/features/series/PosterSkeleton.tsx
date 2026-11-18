import { Skeleton, SkeletonGroup } from '@/design/Skeleton'

/** The shape of the poster grid of Movies and Series while it loads: the same columns and the same 2:3 cards, so nothing moves when the posters arrive. */
export function PosterSkeleton({ label }: { label: string }) {
  return (
    <SkeletonGroup label={label} className="m-0 grid grid-cols-2 gap-3 p-0 sm:grid-cols-[repeat(auto-fill,minmax(190px,1fr))]">
      {Array.from({ length: 12 }, (_, i) => <Skeleton key={i} className="aspect-[2/3] rounded-[23px]" />)}
    </SkeletonGroup>
  )
}

import { useNavigate } from '@tanstack/react-router'
import { PageHead } from '@/design/PageHead'
import { FaceGroups } from './FaceGroups'

/** People: the faces found in your photos, grouped, matched to performer pictures, and named. */
export function PeoplePage() {
  const navigate = useNavigate()
  return (
    <section className="mx-auto flex w-full max-w-[1800px] flex-1 flex-col pb-10 pl-4 pr-4 pt-6 sm:pl-[58px] sm:pr-10">
      <PageHead title="People" blurb="Faces found in your photos, grouped by person and matched to performer pictures" />
      <FaceGroups onView={() => void navigate({ to: '/photos' })} />
    </section>
  )
}

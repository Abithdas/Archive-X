import { Link } from '@tanstack/react-router'
import { useState, type ReactNode } from 'react'
import { responsive } from '@/design/responsive'
import { matchLabel, matchTip, type SimilarFields } from './similarity'
import { imagePath, plural } from './studio'

/** Cards for the related-entity tabs: a portrait performer poster and a square studio poster with the name over the bottom of the picture (as in the classic pages). */
export function PosterGrid({ label, children }: { label: string; children: ReactNode }) {
  return <ul role="tabpanel" aria-label={label} className="m-0 grid list-none grid-cols-2 gap-2.5 p-0 pt-1 sm:grid-cols-[repeat(auto-fill,minmax(200px,1fr))]">{children}</ul>
}

const PLACEHOLDER = '/static/icons/ax-placeholder.png'
/** Shown when there is no picture: the app logo, with a soft blurred copy behind it. */
const Fallback = () => (
  <>
    <img src={PLACEHOLDER} alt="" aria-hidden="true" className="absolute inset-0 size-full scale-125 object-cover opacity-60 blur-xl" />
    <img src={PLACEHOLDER} alt="" aria-hidden="true" className="absolute inset-0 size-full object-contain p-6" />
  </>
)

const PILL = 'inline-flex w-fit items-center rounded-full border px-2 py-[3px] text-[11px] font-semibold leading-[13px]'

export function PerformerPoster({ id, name, image, count, similar }: { id: number; name: string; image?: string | null; count: number; similar?: SimilarFields }) {
  const [ok, setOk] = useState(true)
  const match = similar ? matchLabel(similar) : ''
  const src = `/api/performers/${id}/image?v=${encodeURIComponent(String(image ?? ''))}`
  return (
    <li className="card-in m-0">
      <Link to="/performers/$id" params={{ id: String(id) }} aria-label={`Open performer ${name}`} className="group relative block aspect-[2/3] overflow-hidden rounded-[23px] border border-white/[0.13] bg-[#171a1b] shadow-[0_12px_32px_rgb(0_0_0/0.22)] outline-none focus-visible:ring-2 focus-visible:ring-accent">
        {ok ? <img {...responsive(src, '(max-width: 639px) 46vw, 220px', [320, 480, 640])} alt="" loading="lazy" decoding="async" onError={() => setOk(false)} className="absolute inset-0 size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]" />
          : <Fallback />}
        <span className="absolute inset-x-0 bottom-0 flex flex-col items-stretch gap-[3px] bg-[linear-gradient(rgb(0_0_0/0)_0%,rgb(13_15_17/0.34)_18%,rgb(13_15_17/0.88)_62%,rgb(13_15_17/0.97)_100%)] px-[13px] pb-3 pt-12">
          <span className="truncate text-sm font-bold text-white" title={name}>{name}</span>
          {match ? <span title={matchTip(similar!)} className={`${PILL} border-[rgb(50_199_129/0.48)] bg-[rgb(16_44_33/0.67)] text-[#32c781]`}>{match}</span> : null}
          <span className={`${PILL} mt-[3px] border-white/[0.16] bg-white/[0.094] text-white/90`}>{plural(count, 'video')}</span>
        </span>
      </Link>
    </li>
  )
}

export function StudioPoster({ id, name, image, count, noun = 'video' }: { id: number; name: string; image?: string | null; count: number; noun?: string }) {
  const [ok, setOk] = useState(true)
  return (
    <li className="card-in m-0">
      <Link to="/studios/$id" params={{ id: String(id) }} aria-label={`Open studio ${name}`} className="group relative block aspect-square overflow-hidden rounded-[15px] border border-line bg-[#151615] shadow-[0_7px_18px_rgb(0_0_0/0.18)] outline-none focus-visible:ring-2 focus-visible:ring-accent">
        <span className="absolute inset-0 grid place-items-center">
          {ok ? <img {...responsive(imagePath(id, image), '(max-width: 639px) 46vw, 220px', [240, 320, 480, 640])} alt="" loading="lazy" decoding="async" onError={() => setOk(false)} className="h-[67%] w-[82%] object-contain" />
            : <Fallback />}
        </span>
        <span className="absolute inset-x-0 bottom-0 grid gap-1 bg-[linear-gradient(rgb(0_0_0/0),rgb(10_11_10/0.52)_25%,rgb(10_11_10/0.88))] px-3 pb-[11px] pt-[34px]">
          <span className="truncate text-[13px] font-bold text-[#f4f5f2]" title={name}>{name}</span>
          <span className={`${PILL} border-white/[0.17] bg-white/[0.11] px-2 py-1 text-[rgb(245_246_243/0.9)]`}>{plural(count, noun)}</span>
        </span>
      </Link>
    </li>
  )
}

/** A performer that is named on the videos but has no profile of their own yet: same poster, not a link. */
export function NamedPoster({ name, count }: { name: string; count: number }) {
  return (
    <li className="m-0">
      <div className="relative block aspect-[2/3] overflow-hidden rounded-[23px] border border-white/[0.13] bg-[#171a1b] shadow-[0_12px_32px_rgb(0_0_0/0.22)]">
        <Fallback />
        <span className="absolute inset-x-0 bottom-0 flex flex-col gap-[3px] bg-[linear-gradient(rgb(0_0_0/0)_0%,rgb(13_15_17/0.88)_62%,rgb(13_15_17/0.97)_100%)] px-[13px] pb-3 pt-12">
          <span className="truncate text-sm font-bold text-white" title={name}>{name}</span>
          <span className={`${PILL} mt-[3px] border-white/[0.16] bg-white/[0.094] text-white/90`}>{plural(count, 'video')}</span>
        </span>
      </div>
    </li>
  )
}

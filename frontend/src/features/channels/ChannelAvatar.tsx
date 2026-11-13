import { useState } from 'react'
import { cn } from '@/design/cn'
import { BlurImg } from '@/design/BlurImg'

const initialsOf = (title: string) => title.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => Array.from(p)[0] ?? '').join('').toUpperCase() || 'TG'

/** The channel's own profile picture (its Telegram photo, or the one uploaded for it); the initials show while it loads or if the channel has none. */
export function channelPhotoUrl(id: string | number, image?: string | null, width = 120): string {
  const key = encodeURIComponent(String(id))
  const v = encodeURIComponent(String(image || '0'))
  return String(image || '').startsWith('manual-upload:') ? `/api/channels/${key}/image?v=${v}&w=${width * 2}` : `/api/channels/${key}/telegram-photo?v=${v}`
}

export function ChannelAvatar({ id, title, image, className }: { id: string | number; title: string; image?: string | null; className?: string }) {
  const [ok, setOk] = useState(true)
  return (
    <span className={cn('relative grid size-full place-items-center overflow-hidden rounded-full', className)}>
      <span aria-hidden="true">{initialsOf(title)}</span>
      {ok ? <BlurImg src={channelPhotoUrl(id, image)} alt="" loading="lazy" decoding="async" onError={() => setOk(false)} className="absolute inset-0 size-full rounded-full object-cover" /> : null}
    </span>
  )
}

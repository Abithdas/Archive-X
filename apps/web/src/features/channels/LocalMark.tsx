import { Icon } from '@/design/Icon'
import { cn } from '@/design/cn'
import { LOCAL_LABEL } from './local'

/** A small folder icon: the marker for something that lives in a folder on this PC, not in a Telegram channel. */
export function LocalMark({ className, withText = false }: { className?: string; withText?: boolean }) {
  return (
    <span title={LOCAL_LABEL} className={cn('inline-flex shrink-0 items-center gap-1 text-accent', className)}>
      <Icon name="folders" className="size-3.5" />
      {withText ? <span className="text-[11px] font-bold">Local</span> : <span className="sr-only">{LOCAL_LABEL}</span>}
    </span>
  )
}

import { useRef } from 'react'
import { Icon } from './Icon'

/** The bell and its count badge, animated by the Transitions.dev notification-badge snippet: the badge slides on diagonally and pops, and
 *  the bell itself never moves. The number stays visible while the badge closes. */
export function Bell({ count, className }: { count: number; className?: string }) {
  const shown = useRef(count)
  if (count > 0) shown.current = count
  const open = count > 0
  return (
    <span className={`ax-bell relative grid place-items-center ${className ?? ''}`}>
      <Icon name="bell" />
      <span className="t-badge" data-open={open ? 'true' : 'false'} aria-hidden="true">
        <span className="t-badge-dot">{shown.current > 99 ? '99+' : shown.current}</span>
      </span>
    </span>
  )
}

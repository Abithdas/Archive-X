import { useRef } from 'react'

/** The bell and its count badge, animated by the Transitions.dev notification-badge snippet: the badge slides on diagonally and pops, and
 *  the bell itself never moves. The number stays visible while the badge closes. */
export function Bell({ count, className }: { count: number; className?: string }) {
  const shown = useRef(count)
  if (count > 0) shown.current = count
  const open = count > 0
  return (
    <span className={`ax-bell relative grid place-items-center ${className ?? ''}`}>
      <svg width="20" height="20" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
        <path d="M6.23612 14C6.70621 14.4149 7.3237 14.6667 8 14.6667C8.6763 14.6667 9.29379 14.4149 9.76388 14M12 5.33333C12 4.27247 11.5786 3.25505 10.8284 2.50491C10.0783 1.75476 9.06087 1.33333 8 1.33333C6.93913 1.33333 5.92172 1.75476 5.17157 2.50491C4.42143 3.25505 4 4.27247 4 5.33333C4 7.39346 3.48031 8.80397 2.89978 9.73694C2.41008 10.5239 2.16524 10.9174 2.17422 11.0272C2.18416 11.1487 2.20991 11.1951 2.30785 11.2677C2.39631 11.3333 2.79506 11.3333 3.59257 11.3333H12.4074C13.2049 11.3333 13.6037 11.3333 13.6921 11.2677C13.7901 11.1951 13.8158 11.1487 13.8258 11.0272C13.8348 10.9174 13.5899 10.5239 13.1002 9.73694C12.5197 8.80397 12 7.39346 12 5.33333Z" />
      </svg>
      <span className="t-badge" data-open={open ? 'true' : 'false'} aria-hidden="true">
        <span className="t-badge-dot">{shown.current > 99 ? '99+' : shown.current}</span>
      </span>
    </span>
  )
}

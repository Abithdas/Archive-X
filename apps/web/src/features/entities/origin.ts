/** Where a performer or studio page was opened from, so its back bar can lead there and not always to the Performers list. */
export interface Origin { to: string; label: string }

const SECTIONS: Array<[RegExp, Origin]> = [
  [/^\/photos(\/|$)/, { to: '/photos', label: 'Photos' }],
  [/^\/people(\/|$)/, { to: '/people', label: 'People' }],
  [/^\/videos(\/|$)/, { to: '/videos', label: 'Videos' }],
  [/^\/watch\//, { to: '/videos', label: 'Videos' }],
  [/^\/transfers(\/|$)/, { to: '/transfers', label: 'Transfers' }],
  [/^\/operations(\/|$)/, { to: '/operations', label: 'Operations' }],
  [/^\/ai(\/|$)/, { to: '/ai', label: 'Assistant' }],
]

let previous = ''

/** Called by the router on every page change with the page being left. */
export function noteLeft(pathname: string | undefined): void { previous = pathname ?? '' }

/** The section the person came from, or null when it is the entity's own list (or unknown), which is the normal back target. */
export function originFor(own: 'performers' | 'studios', from: string = previous): Origin | null {
  if (from.startsWith(`/${own}`) && !from.startsWith(`/${own}/`)) return null    // came from the list itself
  if (from.startsWith(`/${own}/`)) return null                                     // came from a sibling page: keep the list
  for (const [re, o] of SECTIONS) if (re.test(from)) return o
  return null
}

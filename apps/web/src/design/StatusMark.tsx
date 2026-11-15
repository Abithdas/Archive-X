export type MarkState = 'pending' | 'active' | 'done' | 'error'

/** One mark for a step: an empty ring, a spinning arc, a filled ring with a check drawn in, or a red cross. The same drawing changes state, so a spinner turns into a check. */
export function StatusMark({ state, label }: { state: MarkState; label?: string }) {
  return (
    <svg className="ax-mark" viewBox="0 0 20 20" data-state={state} role="img" aria-label={label ?? ({ pending: 'Waiting', active: 'Working', done: 'Done', error: 'Failed' } as const)[state]}>
      <circle className="ring" cx="10" cy="10" r="8.5" />
      <circle className="arc" cx="10" cy="10" r="8.5" />
      <path className="tick" d="M6 10.4 8.9 13.3 14.2 7.4" />
      <path className="cross" d="M7 7 13 13M13 7 7 13" />
    </svg>
  )
}

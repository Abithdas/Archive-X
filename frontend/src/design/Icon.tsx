/** One glyph from the backend's icon sprite, identical to the classic UI's icons. */
export function Icon({ name, className }: { name: string; className?: string }) {
  return (
    <svg className={`ax-icon ${className ?? ''}`} aria-hidden="true" focusable="false">
      <use href={`/static/icons/ax-icons.svg#ax-${name}`} />
    </svg>
  )
}

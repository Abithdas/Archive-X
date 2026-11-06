import { cn } from './cn'

/** Pill-in-a-tray toggle group (the classic UI's "All / Female / Male" control). */
export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div role="group" aria-label={label} className="inline-flex gap-0.5 rounded-xl border border-line bg-surface p-[3px]">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn('h-8 rounded-[9px] px-3 text-[13px] font-semibold transition-colors', value === o.value ? 'bg-surface-3 text-fg' : 'text-muted hover:text-fg')}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

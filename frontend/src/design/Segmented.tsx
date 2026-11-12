import { useId } from 'react'
import { cn } from './cn'
import { motion, spring } from './motion'

/** Pill-in-a-tray toggle group ("All / Female / Male"); the highlight glides from one choice to the next. */
export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string }) {
  const pill = useId()
  return (
    <div role="group" aria-label={label} className="flex w-full max-w-full gap-0.5 sm:inline-flex sm:w-auto overflow-x-auto rounded-xl border border-line bg-surface p-[3px] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn('relative h-8 flex-auto shrink-0 whitespace-nowrap rounded-[9px] px-1.5 text-[12px] min-[380px]:px-2 min-[380px]:text-[12.5px] sm:flex-none sm:px-3 sm:text-[13px] font-semibold transition-colors', value === o.value ? 'text-fg' : 'text-muted hover:text-fg')}
        >
          {value === o.value ? <motion.span layoutId={pill} transition={spring} aria-hidden="true" className="absolute inset-0 rounded-[9px] bg-surface-3" /> : null}
          <span className="relative">{o.label}</span>
        </button>
      ))}
    </div>
  )
}

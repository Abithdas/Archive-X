import type { ReactNode } from 'react'
import { cn } from './cn'

/** A status word or two with a highlight sweeping across it (Transitions.dev "Shimmer text"). `text` is the same string as the children. */
export function Shimmer({ text, className, children }: { text: string; className?: string; children?: ReactNode }) {
  return <span className={cn('t-shimmer', className)} data-text={text}>{children ?? text}</span>
}

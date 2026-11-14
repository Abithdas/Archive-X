import type { CSSProperties, ReactNode } from 'react'
import { cn } from './cn'

/**
 * Shimmer Skeleton: one block with a highlight that sweeps across it. The sweep uses only `transform`
 * (compositor-only, no layout or paint per frame) and is switched off by prefers-reduced-motion
 * (see styles.css). Text bars size themselves from the inherited line height, so a skeleton placed inside the same
 * element as the real text takes the real height.
 */
export function Skeleton({ className, style }: { className?: string; style?: CSSProperties }) {
  return (
    <span
      aria-hidden="true"
      style={style}
      className={cn(
        'relative block shrink-0 overflow-hidden rounded-md bg-surface-2 [contain:paint]',
        "after:absolute after:inset-0 after:-translate-x-full after:animate-sk-sweep after:content-['']",
        'after:bg-gradient-to-r after:from-transparent after:via-fg/10 after:to-transparent',
        'motion-reduce:after:hidden',
        className,
      )}
    />
  )
}

/** A text bar exactly one line tall (inherits line-height) but thinner than the line box. */
export function SkeletonLine({ width = '100%', className }: { width?: string; className?: string }) {
  return <Skeleton style={{ width, height: '0.72lh', marginBlock: '0.14lh' }} className={cn('rounded', className)} />
}

/** Announces loading once to assistive tech while the visual placeholders stay hidden from it. */
export function SkeletonGroup({ label, children, className }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div role="status" aria-busy="true" className={className}>
      <span className="sr-only">{label}</span>
      {children}
    </div>
  )
}

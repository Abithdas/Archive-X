import type { HTMLAttributes, ReactNode } from 'react'
import { Icon } from './Icon'
import { cn } from './cn'

/** The same empty state everywhere: a soft icon, what is missing, and what to do next. */
export function Empty({ icon = 'images', title, children, action, className, ...rest }: { icon?: string; title?: ReactNode; children?: ReactNode; action?: ReactNode } & Omit<HTMLAttributes<HTMLDivElement>, 'title'>) {
  return (
    <div {...rest} className={cn('grid justify-items-center gap-2 rounded-card border border-dashed border-line px-6 py-12 text-center', className)}>
      <span aria-hidden="true" className="grid size-12 place-items-center rounded-full bg-surface-2 text-muted"><Icon name={icon} className="size-6" /></span>
      {title ? <p className="font-semibold">{title}</p> : null}
      {children ? <div className="max-w-md text-sm text-muted">{children}</div> : null}
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  )
}

import { useState, type ImgHTMLAttributes } from 'react'
import { cn } from './cn'

/** An image that sharpens in (blur to clear, over the shared slow duration) the first time it loads; an already cached image shows at once. */
export function BlurImg({ className, onLoad, ...rest }: ImgHTMLAttributes<HTMLImageElement>) {
  const [ready, setReady] = useState(false)
  return (
    <img
      {...rest}
      ref={(el) => { if (el?.complete && el.naturalWidth > 0) setReady(true) }}
      onLoad={(e) => { setReady(true); onLoad?.(e) }}
      className={cn('transition-[filter,opacity] duration-300 ease-out motion-reduce:transition-none', ready ? 'opacity-100 blur-0' : 'opacity-0 blur-md', className)}
    />
  )
}

import { cn } from './cn'

/** The assistant's "alive" mark: one dot that breathes (grows and brightens, then shrinks and dims), the way a chat shows that an answer is on its way.
 *  It takes the colour of the text around it unless a text colour class is given. */
export function ChatDot({ className, label }: { className?: string; label?: string }) {
  return <span className={cn('ax-chat-dot', className)} {...(label ? { role: 'status', 'aria-label': label } : { 'aria-hidden': true })} />
}

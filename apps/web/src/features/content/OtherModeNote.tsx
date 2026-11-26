import { Icon } from '@/design/Icon'
import { otherModeText, type OtherModeJobs } from './otherMode'

/** One line saying the other content mode has tasks going. Nothing is shown when it has none. */
export function OtherModeNote({ summary }: { summary: OtherModeJobs | undefined | null }) {
  const text = otherModeText(summary)
  if (!text) return null
  return <p role="status" aria-label="Tasks in the other mode" className="mb-3 flex items-center gap-2 rounded-xl border border-line bg-surface-2 px-3 py-2 text-xs text-muted"><Icon name="clock" className="size-3.5 shrink-0" />{text}</p>
}

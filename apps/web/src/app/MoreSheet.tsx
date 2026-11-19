import { Link } from '@tanstack/react-router'
import { useStatus } from '@/api/hooks'
import { Dialog } from '@/design/Dialog'
import { Icon } from '@/design/Icon'
import { useUi } from './ui'
import { useUnseenCount } from './NotificationsPanel'

const ROW = 'flex h-12 w-full items-center gap-3 rounded-xl px-3 text-left text-[15px] font-semibold hover:bg-fg/5'

/** Phone "More" sheet: the destinations that do not fit in the five-slot bottom bar. */
export function MoreSheet() {
  const open = useUi((s) => s.more)
  const { setMore, setNotifications, setPalette } = useUi.getState()
  const running = (useStatus().data?.jobs as Array<{ status?: string }> | undefined)?.filter((j) => /^(RUNNING|ACTIVE|STARTING|RECOVERING|RETRYING)$/i.test(String(j.status))).length ?? 0
  const notices = useUnseenCount()
  const done = () => setMore(false)
  return (
    <Dialog open={open} onClose={done} title="More">
      <div className="grid gap-0.5">
        <Link to="/operations" onClick={done} className={ROW}><Icon name="operations" className="text-muted" />Operations{running ? <span className="ml-auto rounded-full bg-accent/15 px-2 py-0.5 text-xs text-accent">{running} running</span> : null}</Link>
        <Link to="/settings" onClick={done} className={ROW}><Icon name="gear" className="text-muted" />Settings</Link>
        <Link to="/ai" onClick={done} className={ROW}><Icon name="sparkle" className="text-muted" />AI Assistance</Link>
        <button type="button" className={ROW} onClick={() => { done(); setNotifications(true) }}><Icon name="clock" className="text-muted" />Notifications{notices ? <span className="ml-auto rounded-full bg-accent/15 px-2 py-0.5 text-xs text-accent">{notices}</span> : null}</button>
        <button type="button" className={ROW} onClick={() => { done(); setPalette(true) }}><Icon name="search" className="text-muted" />Command palette</button>
      </div>
    </Dialog>
  )
}

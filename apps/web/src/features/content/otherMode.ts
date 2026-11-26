/** What the other content mode has going: only how many tasks, never what they are (the server leaves their names out). */
export interface OtherModeJobs { count: number; running: number }

export function otherModeText(s: OtherModeJobs | undefined | null): string {
  const count = Math.max(0, Math.floor(Number(s?.count) || 0))
  if (!count) return ''
  const running = Math.min(count, Math.max(0, Math.floor(Number(s?.running) || 0)))
  if (running === count) return count === 1 ? 'A task is running in the other mode.' : `${count} tasks are running in the other mode.`
  if (running === 0) return count === 1 ? 'A task is waiting in the other mode.' : `${count} tasks are waiting in the other mode.`
  return `${count} tasks are in the other mode, ${running} of them running.`
}

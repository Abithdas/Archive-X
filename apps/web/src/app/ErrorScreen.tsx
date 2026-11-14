import { Component, type ReactNode } from 'react'
import { toast } from 'sonner'
import { Icon } from '@/design/Icon'
import { recentErrors } from './errorLog'
import { buildReport } from './report'

/** A page file that cannot be fetched (usually an old tab after the app was updated): retrying in place cannot work because the failed import is remembered, so reload instead. */
export const isStaleChunk = (e: unknown) => /dynamically imported module|Importing a module script failed|Loading chunk|error loading dynamically/i.test(e instanceof Error ? e.message : String(e))

const BTN = 'inline-flex h-10 items-center rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5'

/** Shown instead of a blank page when something in the app throws: try again, reload, or copy a report to send. */
export function ErrorScreen({ error, reset }: { error: unknown; reset?: () => void }) {
  const message = error instanceof Error ? error.message : String(error)
  const copy = async () => {
    const report = buildReport({ error, url: window.location.href, userAgent: navigator.userAgent, recent: recentErrors() })
    try { await navigator.clipboard.writeText(report); toast('Report copied') } catch { toast.error('Could not copy. Select the details below instead.') }
  }
  return (
    <div role="alert" className="mx-auto grid max-w-lg justify-items-center gap-3 px-4 py-24 text-center">
      <span className="grid size-14 place-items-center rounded-full bg-bad/15 text-bad"><Icon name="alert" className="size-6" /></span>
      <h1 className="text-xl font-bold">Something went wrong</h1>
      <p className="text-sm text-muted [overflow-wrap:anywhere]">This page hit an unexpected problem. Your library and settings are safe.</p>
      <p className="max-w-full rounded-lg bg-surface-2 px-3 py-2 text-xs text-muted [overflow-wrap:anywhere]">{message}</p>
      <div className="flex flex-wrap justify-center gap-2">
        {reset ? <button type="button" onClick={isStaleChunk(error) ? () => window.location.reload() : reset} className={`${BTN} bg-accent text-accent-fg`}>Try again</button> : null}
        <button type="button" onClick={() => window.location.reload()} className={BTN}>Reload</button>
        <button type="button" onClick={() => void copy()} className={BTN}>Copy report</button>
      </div>
    </div>
  )
}

/** Catches errors thrown while rendering its children. `resetKey` (such as the current page) clears a caught error when it changes. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { error: unknown; failed: boolean }> {
  state = { error: null as unknown, failed: false }
  static getDerivedStateFromError(error: unknown) { return { error, failed: true } }
  componentDidUpdate(prev: { resetKey?: string }) { if (this.state.failed && prev.resetKey !== this.props.resetKey) this.setState({ error: null, failed: false }) }
  render() { return this.state.failed ? <ErrorScreen error={this.state.error} reset={() => this.setState({ error: null, failed: false })} /> : this.props.children }
}

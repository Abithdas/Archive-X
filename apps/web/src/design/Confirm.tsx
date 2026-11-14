import { Dialog } from './Dialog'
import { cn } from './cn'

/** A yes/no question in a dialog. The text keeps its line breaks, so a list of items can be shown. */
export function Confirm({ title, text, ok, cancel = 'Cancel', danger = false, busy = false, onCancel, onOk }: { cancel?: string; title: string; text: string; ok: string; danger?: boolean; busy?: boolean; onCancel: () => void; onOk: () => void }) {
  return (
    <Dialog open onClose={() => !busy && onCancel()} title={title}>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
        <p className="whitespace-pre-line text-sm text-muted [overflow-wrap:anywhere]">{text}</p>
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" disabled={busy} onClick={onCancel} className="h-11 rounded-xl border border-line-2 px-4 text-sm font-semibold">{cancel}</button>
          <button type="button" disabled={busy} onClick={onOk} className={cn('h-11 rounded-xl px-4 text-sm font-bold disabled:opacity-60', danger ? 'bg-bad text-white' : 'bg-accent text-accent-fg')}>{ok}</button>
        </div>
      </div>
    </Dialog>
  )
}

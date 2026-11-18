import { cn } from '@/design/cn'

/** "3 changes not saved" with Discard and Save. It slides in when there is something to save, and out when there is not. */
export function SaveBar({ count, busy, onSave, onDiscard }: { count: number; busy: boolean; onSave: () => void; onDiscard: () => void }) {
  return (
    <div role="region" aria-label="Unsaved changes" aria-hidden={count ? undefined : true} className={cn('sticky bottom-3 z-10 mt-3 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-accent/40 bg-surface px-4 py-3 shadow-[0_10px_30px_rgb(0_0_0/0.35)] transition-[opacity,transform] duration-300 ease-out', count ? 'translate-y-0 opacity-100' : 'pointer-events-none h-0 translate-y-3 overflow-hidden border-0 p-0 opacity-0')}>
      <p role="status" aria-live="polite" className="text-sm font-semibold">{count ? `${count.toLocaleString()} change${count === 1 ? '' : 's'} not saved` : ''}</p>
      <div className="flex gap-2">
        <button type="button" onClick={onDiscard} disabled={busy} className="h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold transition-transform active:scale-95 disabled:opacity-60">Discard</button>
        <button type="button" onClick={onSave} disabled={busy} className="h-10 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg transition-transform active:scale-95 disabled:opacity-60">{busy ? 'Saving…' : 'Save changes'}</button>
      </div>
    </div>
  )
}

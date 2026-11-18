import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { toast } from 'sonner'
import { api } from '@/api/client'
import { cn } from '@/design/cn'

interface Choice { id: string; label: string; size: string; note: string }
interface ModelState { ok: boolean; model: string; choices: Choice[]; speed: number; state: string; device: string }

/** What the speed means for a person: real time is 1, so under 1 the captions fall behind the picture. */
export function speedText(speed: number, device: string): { text: string; slow: boolean } {
  if (!speed) return { text: 'Not measured yet: it is measured on the first captions you watch.', slow: false }
  const where = device === 'cuda' ? ' on the graphics card' : device === 'cpu' ? ' on the processor' : ''
  if (speed < 1) return { text: `${speed.toFixed(1)}× real time${where}: slower than the picture, so captions fall behind. Choose a smaller model.`, slow: true }
  if (speed < 2.5) return { text: `${speed.toFixed(1)}× real time${where}: keeps up, with little to spare.`, slow: false }
  return { text: `${speed.toFixed(1)}× real time${where}: keeps up easily.`, slow: false }
}

/** Settings, Experience: which speech model makes the live captions. */
export function CaptionModel() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['captions-model'], queryFn: () => api<ModelState>('/api/captions/model'), staleTime: 10_000, retry: false })
  const [busy, setBusy] = useState(false)
  if (q.isError || !q.data || !Array.isArray(q.data.choices)) return null
  const d = q.data
  const now = d.choices.find((c) => c.id === d.model)
  const sp = speedText(d.speed, d.device)
  const change = async (model: string) => {
    setBusy(true)
    try {
      await api('/api/captions/model', { method: 'POST', json: { model } })
      await qc.invalidateQueries({ queryKey: ['captions-model'] })
      toast(`Live captions will use the ${d.choices.find((c) => c.id === model)?.label.toLowerCase()} model. It is downloaded the first time (${d.choices.find((c) => c.id === model)?.size}).`)
    } catch { toast.error('Could not change the caption model.') } finally { setBusy(false) }
  }
  return (
    <section aria-label="Caption model" className="mt-4 rounded-2xl border border-line p-4">
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0"><b className="block text-[15px]">Live caption model</b><small className="text-xs text-muted">The speech model that writes captions from the sound</small></div>
        <select aria-label="Live caption model" value={d.model} disabled={busy} onChange={(e) => void change(e.target.value)} className="h-10 w-44 shrink-0 rounded-xl border border-line-2 bg-bg px-3 text-sm font-medium sm:w-64">
          {d.choices.map((c) => <option key={c.id} value={c.id}>{c.label} · {c.size}</option>)}
        </select>
      </div>
      {now ? <p className="mt-2 text-xs text-muted">{now.note}</p> : null}
      <p role="status" className={cn('mt-1 text-xs', sp.slow ? 'text-warn' : 'text-muted')}>{sp.text}</p>
    </section>
  )
}

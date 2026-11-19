import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { toast } from 'sonner'
import { api } from '@/api/client'
import { Confirm } from '@/design/Confirm'
import { Icon } from '@/design/Icon'
import { errorText } from '@/features/content/useContentMode'

export interface AddonList { type: 'movie' | 'series' | 'anime'; id: string; name: string }
export interface Addon { id: string; url: string; name: string; description: string; version: string; lists: AddonList[]; error?: string }
export interface AddonsView { builtin: Array<{ name: string; what: string }>; items: Addon[] }

const BOX = 'rounded-[18px] border border-line bg-surface p-5'
export const addonsKey = ['screen-addons'] as const
export const useAddons = (enabled = true) => useQuery({ queryKey: addonsKey, queryFn: () => api<AddonsView>('/api/screen/addons'), staleTime: 60_000, enabled })

/**
 * Settings, Addons: the catalogue add-ons Discover uses. Three come with the app; more can be added by pasting an add-on's address (a link ending in
 * manifest.json, or a stremio:// link). Only the add-on's lists of movies and series are read; it is asked which list you open, nothing else.
 */
export function AddonsPanel() {
  const qc = useQueryClient()
  const view = useAddons()
  const [url, setUrl] = useState('')
  const [asking, setAsking] = useState<Addon | null>(null)
  const add = useMutation({
    mutationFn: (u: string) => api<AddonsView>('/api/screen/addons', { method: 'POST', json: { url: u } }),
    onSuccess: (v) => { qc.setQueryData(addonsKey, v); setUrl(''); toast(`Added ${v.items[v.items.length - 1]?.name ?? 'the add-on'}`) },
  })
  const remove = useMutation({
    mutationFn: (id: string) => api<AddonsView>(`/api/screen/addons/${id}`, { method: 'DELETE' }),
    onSuccess: (v) => { qc.setQueryData(addonsKey, v); setAsking(null); toast('Add-on removed') },
  })
  const submit = (e: FormEvent) => { e.preventDefault(); if (url.trim()) add.mutate(url.trim()) }
  const items = view.data?.items ?? []
  return (
    <div className="grid gap-4">
      <section aria-label="Built in" className={BOX}>
        <h2 className="text-lg font-bold">Built in</h2>
        <p className="mb-3 text-sm text-muted">These come with the app and fill Discover. They are switched off together with Cinemeta in Metadata.</p>
        <ul className="m-0 grid list-none gap-2 p-0">
          {(view.data?.builtin ?? []).map((b) => <li key={b.name} className="flex items-start gap-3 rounded-xl border border-line px-3.5 py-3"><Icon name="database" className="mt-0.5 size-4 shrink-0 text-muted" /><span className="min-w-0"><span className="block text-sm font-semibold">{b.name}</span><span className="block text-xs text-muted">{b.what}</span></span></li>)}
        </ul>
      </section>
      <section aria-label="Your add-ons" className={BOX}>
        <h2 className="text-lg font-bold">Your add-ons</h2>
        <p className="mb-3 text-sm text-muted">Add a catalogue add-on to get its lists in Discover (under Filters, List). Only its lists of movies and series are read; its streams are never asked for. What an add-on lists is its own choice: Archive X cannot check it.</p>
        {view.isError ? <p role="alert" className="mb-3 text-sm text-warn">Could not read the add-ons.</p> : null}
        {items.length ? (
          <ul className="m-0 mb-4 grid list-none gap-2 p-0">
            {items.map((a) => (
              <li key={a.id} className="grid gap-1.5 rounded-xl border border-line px-3.5 py-3">
                <div className="flex items-start justify-between gap-3">
                  <span className="min-w-0"><span className="block text-sm font-semibold">{a.name}{a.version ? <span className="ml-2 text-xs font-normal text-faint">v{a.version}</span> : null}</span>
                    {a.description ? <span className="block text-xs text-muted">{a.description}</span> : null}</span>
                  <button type="button" onClick={() => setAsking(a)} aria-label={`Remove ${a.name}`} className="h-9 shrink-0 rounded-lg border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5">Remove</button>
                </div>
                {a.error ? <p role="status" className="text-xs text-warn">{a.error}</p>
                  : <ul aria-label={`Lists of ${a.name}`} className="m-0 flex list-none flex-wrap gap-1.5 p-0">{a.lists.map((l) => <li key={`${l.type}:${l.id}`} className="rounded-full bg-fg/10 px-2.5 py-1 text-xs font-medium">{l.name} <span className="text-faint">· {l.type}</span></li>)}</ul>}
                <p className="truncate text-[11px] text-faint" title={a.url}>{a.url}</p>
              </li>
            ))}
          </ul>
        ) : <p className="mb-4 text-sm text-muted">None added yet.</p>}
        <form onSubmit={submit} className="grid gap-2" aria-label="Add an add-on">
          <label className="text-sm font-semibold" htmlFor="addon-url">Add-on address</label>
          <div className="flex flex-wrap gap-2">
            <input id="addon-url" type="url" inputMode="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://… /manifest.json or stremio://…" autoComplete="off" spellCheck={false}
              className="h-12 min-w-0 flex-1 rounded-xl border border-line-2 bg-bg px-3.5 text-[15px] text-fg outline-none placeholder:text-faint focus:border-accent/60" />
            <button type="submit" disabled={add.isPending || !url.trim()} className="h-12 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60">{add.isPending ? 'Checking…' : 'Add'}</button>
          </div>
          {add.isError ? <p role="alert" className="text-sm text-warn">{errorText(add.error, 'Could not add that add-on.')}</p> : null}
        </form>
      </section>
      {asking ? <Confirm title={`Remove ${asking.name}?`} text="Its lists leave Discover. You can add it again with its address." ok="Remove" danger busy={remove.isPending} onCancel={() => setAsking(null)} onOk={() => remove.mutate(asking.id)} /> : null}
    </div>
  )
}

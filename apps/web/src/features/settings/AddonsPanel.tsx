import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { toast } from 'sonner'
import { StreamingActions, serverInfoKey } from './StreamingSetup'
import { api } from '@/api/client'
import { Confirm } from '@/design/Confirm'
import { Icon } from '@/design/Icon'
import { Switch } from '@/design/Switch'
import { errorText } from '@/features/content/useContentMode'

export interface AddonList { type: 'movie' | 'series' | 'anime'; id: string; name: string }
export interface Addon { id: string; url: string; name: string; description: string; version: string; lists: AddonList[]; provides?: string[]; note?: string; configurable?: boolean; error?: string }
export interface BuiltinAddon { id: string; name: string; what: string }
export interface AddonsView { builtin: BuiltinAddon[]; removed?: BuiltinAddon[]; items: Addon[]; allow_local?: boolean }

const BOX = 'rounded-[18px] border border-line bg-surface p-5'
export const addonsKey = ['screen-addons'] as const
export interface StreamingServer { url: string; default: string; saved: boolean; up: boolean; installed?: boolean; download?: string }
export const serverKey = serverInfoKey
export const useAddons = (enabled = true) => useQuery({ queryKey: addonsKey, queryFn: () => api<AddonsView>('/api/screen/addons'), staleTime: 60_000, enabled })

/**
 * Settings, Addons: the catalogue add-ons Discover uses. Three come with the app (they can be removed too, and added back); more can be added by pasting an add-on's address (a link ending in
 * manifest.json, or a stremio:// link). Lists of movies and series fill Discover; a title's page also asks for streams, subtitles and details.
 */
export function AddonsPanel() {
  const qc = useQueryClient()
  const view = useAddons()
  const [url, setUrl] = useState('')
  const [asking, setAsking] = useState<{ id: string; name: string; builtin: boolean } | null>(null)
  const server = useQuery({ queryKey: serverKey, queryFn: () => api<StreamingServer>('/api/screen/streaming-server'), staleTime: 15_000, retry: false })
  const [addr, setAddr] = useState('')
  const saveServer = useMutation({
    mutationFn: (u: string) => api<StreamingServer>('/api/screen/streaming-server', { method: 'POST', json: { url: u } }),
    onSuccess: (v) => { qc.setQueryData(serverKey, v); setAddr(''); toast(v.up ? 'Streaming server found' : 'Saved. No streaming server answers there yet.'); void qc.invalidateQueries({ queryKey: ['addon-streams'] }) },
  })
  const [checking, setChecking] = useState(false)
  const checkServer = async () => { setChecking(true); try { await qc.invalidateQueries({ queryKey: serverKey }); await qc.invalidateQueries({ queryKey: ['addon-streams'] }) } finally { setChecking(false) } }
  const add = useMutation({
    mutationFn: (u: string) => api<AddonsView>('/api/screen/addons', { method: 'POST', json: { url: u } }),
    onSuccess: (v) => { qc.setQueryData(addonsKey, v); setUrl(''); toast(`Added ${v.items[v.items.length - 1]?.name ?? 'the add-on'}`) },
  })
  const remove = useMutation({
    mutationFn: (id: string) => api<AddonsView>(`/api/screen/addons/${id}`, { method: 'DELETE' }),
    onSuccess: (v) => { qc.setQueryData(addonsKey, v); setAsking(null); toast('Add-on removed') },
  })
  const restore = useMutation({
    mutationFn: (id: string) => api<AddonsView>(`/api/screen/addons/${id}/restore`, { method: 'POST', json: {} }),
    onSuccess: (v) => { qc.setQueryData(addonsKey, v); toast('Add-on added back') },
  })
  const local = useMutation({
    mutationFn: (on: boolean) => api<AddonsView>('/api/screen/addons/settings', { method: 'POST', json: { allow_local: on } }),
    onSuccess: (v) => qc.setQueryData(addonsKey, v),
  })
  const submit = (e: FormEvent) => { e.preventDefault(); if (url.trim()) add.mutate(url.trim()) }
  const items = view.data?.items ?? []
  return (
    <div className="grid gap-4">
      <section aria-label="Built in" className={BOX}>
        <h2 className="text-lg font-bold">Built in</h2>
        <p className="mb-3 text-sm text-muted">These come with the app and fill Discover. Remove the ones you do not want; a removed one can be added back. All of them are also switched off with Cinemeta in Metadata.</p>
        {(view.data?.builtin ?? []).length ? (
          <ul className="m-0 grid list-none gap-2 p-0">
            {(view.data?.builtin ?? []).map((b) => (
              <li key={b.id} className="flex items-start gap-3 rounded-xl border border-line px-3.5 py-3">
                <Icon name="database" className="mt-0.5 size-4 shrink-0 text-muted" />
                <span className="min-w-0 flex-1"><span className="block text-sm font-semibold">{b.name}</span><span className="block text-xs text-muted">{b.what}</span></span>
                <button type="button" onClick={() => setAsking({ id: b.id, name: b.name, builtin: true })} aria-label={`Remove ${b.name}`} className="h-9 shrink-0 rounded-lg border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5">Remove</button>
              </li>
            ))}
          </ul>
        ) : view.data ? <p className="text-sm text-muted">All built-in add-ons are removed.</p> : null}
        {(view.data?.removed ?? []).length ? (
          <div className="mt-4 grid gap-2">
            <h3 className="text-sm font-bold">Removed</h3>
            <ul aria-label="Removed built-in add-ons" className="m-0 grid list-none gap-2 p-0">
              {(view.data?.removed ?? []).map((b) => (
                <li key={b.id} className="flex items-start gap-3 rounded-xl border border-dashed border-line px-3.5 py-3">
                  <span className="min-w-0 flex-1"><span className="block text-sm font-semibold text-muted">{b.name}</span><span className="block text-xs text-faint">{b.what}</span></span>
                  <button type="button" disabled={restore.isPending} onClick={() => restore.mutate(b.id)} aria-label={`Add back ${b.name}`} className="h-9 shrink-0 rounded-lg border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5 disabled:opacity-60">Add back</button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>
      <section aria-label="Your add-ons" className={BOX}>
        <h2 className="text-lg font-bold">Your add-ons</h2>
        <p className="mb-3 text-sm text-muted">Add any Stremio add-on by its address. Its lists of movies and series show in Discover (under Filters, List); what else it provides is named below. A title's page asks the add-ons that have streams for them: direct streams play here, and torrents play through Stremio's streaming server (below). What an add-on lists is its own choice: Archive X cannot check it.</p>
        {view.isError ? <p role="alert" className="mb-3 text-sm text-warn">Could not read the add-ons.</p> : null}
        {items.length ? (
          <ul className="m-0 mb-4 grid list-none gap-2 p-0">
            {items.map((a) => (
              <li key={a.id} className="grid gap-1.5 rounded-xl border border-line px-3.5 py-3">
                <div className="flex items-start justify-between gap-3">
                  <span className="min-w-0"><span className="block text-sm font-semibold">{a.name}{a.version ? <span className="ml-2 text-xs font-normal text-faint">v{a.version}</span> : null}</span>
                    {a.description ? <span className="block text-xs text-muted">{a.description}</span> : null}</span>
                  <button type="button" onClick={() => setAsking({ id: a.id, name: a.name, builtin: false })} aria-label={`Remove ${a.name}`} className="h-9 shrink-0 rounded-lg border border-line-2 px-3 text-xs font-semibold hover:bg-fg/5">Remove</button>
                </div>
                {a.error ? <p role="status" className="text-xs text-warn">{a.error}</p>
                  : (<>
                    {a.provides?.length ? <p className="text-xs text-muted">Provides: {a.provides.join(', ')}</p> : null}
                    {a.lists.length ? <ul aria-label={`Lists of ${a.name}`} className="m-0 flex list-none flex-wrap gap-1.5 p-0">{a.lists.map((l) => <li key={`${l.type}:${l.id}`} className="rounded-full bg-fg/10 px-2.5 py-1 text-xs font-medium">{l.name} <span className="text-faint">· {l.type}</span></li>)}</ul> : null}
                    {a.note ? <p role="status" className="text-xs text-faint">{a.note}</p> : null}
                    {a.configurable ? <a href={`${a.url}/configure`} target="_blank" rel="noreferrer" className="w-fit text-xs font-semibold text-accent underline">Configure this add-on ↗</a> : null}
                  </>)}
                <p className="truncate text-[11px] text-faint" title={a.url}>{a.url}</p>
              </li>
            ))}
          </ul>
        ) : <p className="mb-4 text-sm text-muted">None added yet.</p>}
        <div className="mb-4 flex items-start gap-3 rounded-xl border border-line px-3.5 py-3">
          <Switch label="Allow add-ons on this PC or network" checked={Boolean(view.data?.allow_local)} onChange={(v) => local.mutate(v)} />
          <div className="min-w-0 flex-1"><p className="text-sm font-semibold">Allow add-ons on this PC or network</p><p className="text-xs text-muted">Lets you add an add-on you run yourself, such as http://127.0.0.1:7000, a NAS or a media server. Off, only https:// add-ons on the internet are accepted.</p></div>
        </div>
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
      <section aria-label="Streaming server" className={BOX}>
        <h2 className="text-lg font-bold">Streaming server for torrents</h2>
        <p className="mb-3 text-sm text-muted">Torrent streams (Torrentio and similar add-ons) are played by Stremio's streaming server, the small program Stremio itself uses. Start Stremio, or its streaming service, on this PC (or on a computer in your network) and Archive X plays torrents through it. Archive X has no torrent code of its own.</p>
        {server.data ? (
          <p role="status" className={'mb-3 flex items-center gap-2 text-sm font-semibold ' + (server.data.up ? 'text-good' : 'text-warn')}>
            <span aria-hidden="true" className={'size-2 rounded-full ' + (server.data.up ? 'bg-good' : 'bg-warn')} />
            {server.data.up ? `Running at ${server.data.url}` : `Not found at ${server.data.url}`}
          </p>
        ) : server.isError ? <p role="alert" className="mb-3 text-sm text-warn">Could not check the streaming server.</p> : null}
        <div className="mb-3 flex flex-wrap gap-2">
          <button type="button" disabled={checking} onClick={() => void checkServer()} className="h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-60">{checking ? 'Checking…' : 'Check again'}</button>
          {server.data?.saved ? <button type="button" disabled={saveServer.isPending} onClick={() => saveServer.mutate('')} className="h-10 rounded-xl border border-line-2 px-4 text-sm font-semibold hover:bg-fg/5 disabled:opacity-60">Use the usual address</button> : null}
        </div>
        <div className="mb-3"><StreamingActions onUp={() => void checkServer()} /></div>
        <form onSubmit={(e) => { e.preventDefault(); if (addr.trim()) saveServer.mutate(addr.trim()) }} className="grid gap-2" aria-label="Streaming server address">
          <label className="text-sm font-semibold" htmlFor="streaming-server-url">Address (usually http://127.0.0.1:11470)</label>
          <div className="flex flex-wrap gap-2">
            <input id="streaming-server-url" type="text" inputMode="url" value={addr} onChange={(e) => setAddr(e.target.value)} placeholder={server.data?.default ?? 'http://127.0.0.1:11470'} autoComplete="off" spellCheck={false}
              className="h-12 min-w-0 flex-1 rounded-xl border border-line-2 bg-bg px-3.5 text-[15px] text-fg outline-none placeholder:text-faint focus:border-accent/60" />
            <button type="submit" disabled={saveServer.isPending || !addr.trim()} className="h-12 rounded-xl bg-accent px-5 text-sm font-bold text-accent-fg disabled:opacity-60">{saveServer.isPending ? 'Saving…' : 'Save'}</button>
          </div>
          {saveServer.isError ? <p role="alert" className="text-sm text-warn">{errorText(saveServer.error, 'Could not save that address.')}</p> : null}
        </form>
      </section>
      {asking ? <Confirm title={`Remove ${asking.name}?`} text={asking.builtin ? 'Its lists leave Discover. You can add it back here at any time.' : 'Its lists leave Discover. You can add it again with its address.'} ok="Remove" danger busy={remove.isPending} onCancel={() => setAsking(null)} onOk={() => remove.mutate(asking.id)} /> : null}
    </div>
  )
}

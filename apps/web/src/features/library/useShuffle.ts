import { useState } from 'react'
import { toast } from 'sonner'
import { ApiError, api } from '@/api/client'
import { NOT_ON_LAN, downloadedMessage, externalBody, failedMessage, isAndroid, isIOS, isRemoteHost, mobileVlcUrl, playable, playlistItems, routeFor, shuffled, startedMessage, startingMessage, type Playable, type Player } from './shuffle'

const detail = (e: unknown) => (e instanceof ApiError ? e.detail : e instanceof Error ? e.message : '')

function saveText(text: string, filename: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.style.display = 'none'
  document.body.appendChild(a); a.click(); a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 10_000)
}

/** Shuffle the given videos into one VLC / MPC-HC playlist. `getVideos` may be async (the Library gathers every page). */
export function useShuffle() {
  const [busy, setBusy] = useState(false)
  const run = async (player: Player, getVideos: () => Promise<readonly Playable[]> | readonly Playable[], emptyMessage: string) => {
    if (busy) return
    setBusy(true)
    try {
      const list = playable(await getVideos())
      if (!list.length) return void toast(emptyMessage)
      const items = playlistItems(shuffled(list))
      const remote = isRemoteHost(location.hostname)
      const ua = navigator.userAgent || ''
      const ios = isIOS(ua, navigator.platform || '', navigator.maxTouchPoints || 0), android = isAndroid(ua)
      const route = routeFor(player, remote, ios || android)
      toast(startingMessage(player, items.length))
      if (route === 'launch') {
        const r = await api<{ ok?: boolean; player?: string; count?: number; detail?: string }>('/api/play/external', { method: 'POST', json: externalBody(player, items) })
        if (!r?.ok) return void toast.error(failedMessage(player, r?.detail))
        toast(startedMessage(r, player, items.length, remote), { duration: 6000 })
      } else if (route === 'mobile-vlc') {
        const r = await api<{ url?: string; lan?: boolean; detail?: string }>('/api/play/device-playlist-link', { method: 'POST', json: { items } })
        if (!r?.url) return void toast.error(r?.detail || 'Could not create the VLC playlist.')
        if (!r.lan) return void toast.error(NOT_ON_LAN)
        const handoff = mobileVlcUrl(r.url, android, ios)
        if (handoff) location.href = handoff
      } else {
        const res = await fetch('/api/play/device-playlist', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ items }) })
        if (!res.ok) return void toast.error('Could not create the playlist.')
        saveText(await res.text(), 'archive-x-random.m3u', 'audio/x-mpegurl')
        toast(downloadedMessage(player), { duration: 6000 })
      }
    } catch (e) {
      toast.error(failedMessage(player, detail(e)))
    } finally { setBusy(false) }
  }
  return { busy, run }
}

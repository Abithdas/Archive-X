import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { LibraryItem } from '@/api/types'
import { CardMenu, deleteVideo, openExternal } from './CardMenu'

const item = { id: 77, _cid: '-1001', media_id: 5, name: 'Scene', filename: 'f.mp4', size: 1, duration: 60, width: 1, height: 1, resolution_category: '1080p', thumbnail_id: 77, favorite: 0, upload_date: null, channel_title: 'Main', performers: [], studios: [], tags: [], studio: '' } as LibraryItem
beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
  HTMLElement.prototype.hidePopover = function () {}
})
afterEach(() => vi.unstubAllGlobals())
const bodyOf = (call: unknown[]) => JSON.parse(String((call[1] as RequestInit).body))

describe('card actions', () => {
  it('asks the backend to open the video in the chosen player', async () => {
    const f = vi.fn(async (_url: string, _init?: RequestInit) => new Response('{"ok":true,"player":"VLC"}', { status: 200 }))
    vi.stubGlobal('fetch', f)
    await openExternal(item, 'vlc')
    expect(f.mock.calls[0]?.[0]).toBe('/api/play/external')
    expect(bodyOf(f.mock.calls[0]!)).toEqual({ channel_id: '-1001', message_id: 77, player: 'vlc' })
  })
  it('surfaces the backend reason when the player is missing', async () => {
    vi.stubGlobal('fetch', vi.fn(async (_url: string, _init?: RequestInit) => new Response('{"ok":false,"detail":"VLC not found"}', { status: 200 })))
    await expect(openExternal(item, 'vlc')).rejects.toThrow('VLC not found')
  })
  it('deletes with an explicit confirmation flag and numeric channel id', async () => {
    const f = vi.fn(async (_url: string, _init?: RequestInit) => new Response('{"ok":true}', { status: 200 }))
    vi.stubGlobal('fetch', f)
    await deleteVideo(item)
    expect(bodyOf(f.mock.calls[0]!)).toEqual({ channel_id: -1001, message_id: 77, confirm_delete: true })
  })
})

describe('CardMenu', () => {
  it('never deletes without going through the confirm dialog', async () => {
    const f = vi.fn(async (_url: string, _init?: RequestInit) => new Response('{"ok":true}', { status: 200 }))
    vi.stubGlobal('fetch', f)
    render(<QueryClientProvider client={new QueryClient()}><CardMenu item={item} /></QueryClientProvider>)
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete', hidden: true }))
    expect(f).not.toHaveBeenCalled()
    expect(screen.getByText('Delete this video from Telegram?')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(f).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete', hidden: true }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete video' }))
    await waitFor(() => expect(f).toHaveBeenCalledTimes(1))
    expect(f.mock.calls[0]?.[0]).toBe('/api/library/delete')
  })
})

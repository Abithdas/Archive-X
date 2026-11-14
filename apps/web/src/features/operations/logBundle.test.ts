import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/api/client'
import { downloadLogBundle, folderOf } from './logBundle'

const setBridge = (on: boolean) => {
  if (on) (window as unknown as { __TAURI_INTERNALS__: unknown }).__TAURI_INTERNALS__ = { invoke: vi.fn() }
  else delete (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__
}
afterEach(() => { setBridge(false); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('folderOf', () => {
  it('gives the folder of a Windows or Unix path', () => {
    expect(folderOf('C:\\Users\\Abith-PC\\Downloads\\archive-x-logs-2026-11-14_08-12-45.zip')).toBe('C:\\Users\\Abith-PC\\Downloads')
    expect(folderOf('/home/me/Downloads/a.zip')).toBe('/home/me/Downloads')
    expect(folderOf('a.zip')).toBe('a.zip')
  })
})

describe('downloadLogBundle', () => {
  it('in the desktop app asks the server to save it into Downloads and reports where', async () => {
    setBridge(true)
    const fetchMock = vi.fn(async (_url: string) => new Response(JSON.stringify({ ok: true, name: 'archive-x-logs-2026-11-14_08-12-45.zip', path: 'C:\\Users\\me\\Downloads\\archive-x-logs-2026-11-14_08-12-45.zip', files: 9 }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const r = await downloadLogBundle()
    expect(fetchMock.mock.calls[0]![0]).toBe('/api/diagnostics/logs.zip?save=1')
    expect(r).toEqual({ kind: 'saved', name: 'archive-x-logs-2026-11-14_08-12-45.zip', path: 'C:\\Users\\me\\Downloads\\archive-x-logs-2026-11-14_08-12-45.zip', files: 9 })
  })

  it('in a browser downloads the zip under the name the server chose', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(new Blob(['PK']), { status: 200, headers: { 'Content-Disposition': 'attachment; filename="archive-x-logs-2026-11-14_08-12-45.zip"' } })))
    const create = vi.fn(() => 'blob:logs')
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() })
    let clicked: HTMLAnchorElement | undefined
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { clicked = this })
    const r = await downloadLogBundle()
    expect(r).toEqual({ kind: 'downloaded', name: 'archive-x-logs-2026-11-14_08-12-45.zip' })
    expect(create).toHaveBeenCalledOnce()
    expect(clicked?.download).toBe('archive-x-logs-2026-11-14_08-12-45.zip')
    expect(clicked?.href).toBe('blob:logs')
    expect(document.querySelector('a[download]')).toBeNull()   // the temporary link is removed again
  })

  it('reads the standard encoded file name form too', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(new Blob(['PK']), { status: 200, headers: { 'Content-Disposition': "attachment; filename*=utf-8''archive-x-logs-2026-01-02_03-04-05.zip" } })))
    Object.assign(URL, { createObjectURL: () => 'blob:x', revokeObjectURL: vi.fn() })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    expect((await downloadLogBundle())).toEqual({ kind: 'downloaded', name: 'archive-x-logs-2026-01-02_03-04-05.zip' })
  })

  it('turns a server refusal into an error with its reason', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'Could not make the log bundle: disk full' }), { status: 500 })))
    await expect(downloadLogBundle()).rejects.toMatchObject({ name: 'ApiError', status: 500, detail: 'Could not make the log bundle: disk full' })
    await expect(downloadLogBundle()).rejects.toBeInstanceOf(ApiError)
  })
})

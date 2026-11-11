import { api, libraryUrl } from '@/api/client'
import type { LibraryItem, LibraryPage, LibraryQuery } from '@/api/types'

export const SERVER_PAGE = 48   // the library endpoint never returns more than 48 per request
export const pageCount = (total: number, per = SERVER_PAGE) => Math.max(1, Math.ceil(Math.max(0, total) / per))

/** Every video matching a Library query (all pages), fetched a few pages at a time. Used by "shuffle everything". */
export async function collectLibrary(query: Omit<LibraryQuery, 'page' | 'per'>, onProgress?: (loaded: number) => void, concurrency = 4): Promise<LibraryItem[]> {
  const one = (page: number) => api<LibraryPage>(libraryUrl({ ...query, page, per: SERVER_PAGE }))
  const first = await one(0)
  const out: LibraryItem[][] = [first.items]
  let loaded = first.items.length
  onProgress?.(loaded)
  const pages = pageCount(first.total)
  for (let start = 1; start < pages; start += concurrency) {
    const batch = await Promise.all(Array.from({ length: Math.min(concurrency, pages - start) }, (_, i) => one(start + i)))
    batch.forEach((b, i) => { out[start + i] = b.items; loaded += b.items.length })
    onProgress?.(loaded)
  }
  return out.flat()
}

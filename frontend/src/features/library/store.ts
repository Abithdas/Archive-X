import { create } from 'zustand'
import type { ResolutionKey, SortKey } from '@/api/types'

const KEY = 'ax_next_library_channels'

function load(): string[] {
  try {
    const raw = localStorage.getItem(KEY)
    const parsed: unknown = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : []
  } catch {
    return []
  }
}

interface LibraryState {
  channels: string[]
  page: number
  sort: SortKey
  favorites: boolean
  resolution: ResolutionKey
  setSort: (s: SortKey) => void
  setFavorites: (v: boolean) => void
  setResolution: (r: ResolutionKey) => void
  resetFilters: () => void
  toggle: (id: string) => void
  selectAll: (ids: string[]) => void
  clear: () => void
  setPage: (page: number) => void
}

const persist = (channels: string[]) => {
  try {
    localStorage.setItem(KEY, JSON.stringify(channels))
  } catch {
    /* private mode */
  }
}

export const useLibraryStore = create<LibraryState>((set, get) => ({
  channels: load(),
  page: 0,
  sort: 'date_desc',
  favorites: false,
  resolution: '',
  setSort: (sort) => set({ sort, page: 0 }),
  setFavorites: (favorites) => set({ favorites, page: 0 }),
  setResolution: (resolution) => set({ resolution, page: 0 }),
  resetFilters: () => set({ favorites: false, resolution: '', sort: 'date_desc', page: 0 }),
  toggle: (id) => {
    const cur = get().channels
    const next = cur.includes(id) ? cur.filter((c) => c !== id) : [...cur, id]
    persist(next)
    set({ channels: next, page: 0 })
  },
  selectAll: (ids) => {
    persist(ids)
    set({ channels: ids, page: 0 })
  },
  clear: () => {
    persist([])
    set({ channels: [], page: 0 })
  },
  setPage: (page) => set({ page: Math.max(0, page) }),
}))

import { create } from 'zustand'
import type { SortKey } from '@/api/types'

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

export interface Filters {
  studio: string
  performer: string
  tag: string
  dateFrom: string
  dateTo: string
  resolution: string
}
export const NO_FILTERS: Filters = { studio: '', performer: '', tag: '', dateFrom: '', dateTo: '', resolution: '' }
export const PER_CHOICES = [12, 24, 36, 48] as const

interface LibraryState {
  channels: string[]
  page: number
  sort: SortKey
  favorites: boolean
  filters: Filters
  per: number
  setSort: (s: SortKey) => void
  setFavorites: (v: boolean) => void
  setFilters: (f: Filters) => void
  removeFilter: (k: keyof Filters) => void
  setPer: (n: number) => void
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
  filters: NO_FILTERS,
  per: 24,
  setSort: (sort) => set({ sort, page: 0 }),
  setFavorites: (favorites) => set({ favorites, page: 0 }),
  setFilters: (filters) => set({ filters, page: 0 }),
  removeFilter: (k) => set((st) => ({ filters: { ...st.filters, [k]: '' }, page: 0 })),
  setPer: (per) => set({ per, page: 0 }),
  resetFilters: () => set({ favorites: false, filters: NO_FILTERS, sort: 'date_desc', page: 0 }),
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

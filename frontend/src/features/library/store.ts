import { create } from 'zustand'

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

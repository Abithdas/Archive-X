import { create } from 'zustand'

/** The header search box. Each page decides what it filters (the classic UI's context-aware search). */
export const useSearch = create<{ q: string; set: (q: string) => void }>((set) => ({ q: '', set: (q) => set({ q }) }))

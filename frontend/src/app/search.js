import { create } from 'zustand';
/** The header search box. Each page decides what it filters (the classic UI's context-aware search). */
export const useSearch = create((set) => ({ q: '', set: (q) => set({ q }) }));

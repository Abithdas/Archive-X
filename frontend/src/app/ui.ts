import { create } from 'zustand'

/** Which overlay is open, plus a counter the header search listens to ("Search library" focuses it). */
interface UiState {
  palette: boolean
  notifications: boolean
  more: boolean
  focusSearch: number
  setPalette: (v: boolean) => void
  setNotifications: (v: boolean) => void
  setMore: (v: boolean) => void
  requestSearchFocus: () => void
}
export const useUi = create<UiState>((set) => ({
  palette: false,
  notifications: false,
  more: false,
  focusSearch: 0,
  setPalette: (palette) => set({ palette }),
  setNotifications: (notifications) => set({ notifications }),
  setMore: (more) => set({ more }),
  requestSearchFocus: () => set((s) => ({ focusSearch: s.focusSearch + 1 })),
}))

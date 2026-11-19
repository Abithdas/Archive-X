import { create } from 'zustand'

/** Which overlay is open, plus a counter the header search listens to ("Search library" focuses it). */
interface UiState {
  palette: boolean
  more: boolean
  notifications: boolean
  focusSearch: number
  /** A page whose first picture runs under the top bar (the Home billboard): the bar floats clear over it until the page is scrolled. */
  immersive: boolean
  setImmersive: (v: boolean) => void
  setPalette: (v: boolean) => void
  setMore: (v: boolean) => void
  setNotifications: (v: boolean) => void
  requestSearchFocus: () => void
}
export const useUi = create<UiState>((set) => ({
  palette: false,
  more: false,
  notifications: false,
  focusSearch: 0,
  immersive: false,
  setImmersive: (immersive) => set({ immersive }),
  setPalette: (palette) => set({ palette }),
  setMore: (more) => set({ more }),
  setNotifications: (notifications) => set({ notifications }),
  requestSearchFocus: () => set((s) => ({ focusSearch: s.focusSearch + 1 })),
}))

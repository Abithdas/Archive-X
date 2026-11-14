import { create } from 'zustand';
export const useUi = create((set) => ({
    palette: false,
    notifications: false,
    more: false,
    focusSearch: 0,
    setPalette: (palette) => set({ palette }),
    setNotifications: (notifications) => set({ notifications }),
    setMore: (more) => set({ more }),
    requestSearchFocus: () => set((s) => ({ focusSearch: s.focusSearch + 1 })),
}));

import { create } from 'zustand';
const KEY = 'ax_next_library_channels';
function load() {
    try {
        const raw = localStorage.getItem(KEY);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed.filter((v) => typeof v === 'string') : [];
    }
    catch {
        return [];
    }
}
export const NO_FILTERS = { studio: '', performer: '', tag: '', dateFrom: '', dateTo: '', resolution: '' };
export const PER_CHOICES = [12, 24, 36, 48];
const persist = (channels) => {
    try {
        localStorage.setItem(KEY, JSON.stringify(channels));
    }
    catch {
        /* private mode */
    }
};
export const useLibraryStore = create((set, get) => ({
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
        const cur = get().channels;
        const next = cur.includes(id) ? cur.filter((c) => c !== id) : [...cur, id];
        persist(next);
        set({ channels: next, page: 0 });
    },
    selectAll: (ids) => {
        persist(ids);
        set({ channels: ids, page: 0 });
    },
    clear: () => {
        persist([]);
        set({ channels: [], page: 0 });
    },
    setPage: (page) => set({ page: Math.max(0, page) }),
}));

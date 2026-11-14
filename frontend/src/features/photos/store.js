import { create } from 'zustand';
import { readFavorites, toggleFavorite, writeFavorites } from './photos';
const storage = () => { try {
    return window.localStorage;
}
catch {
    return null;
} };
/** Photo page choices; kept for the session like the classic page, favourites persist in this browser. */
export const usePhotoStore = create((set, get) => ({
    contentTags: [], setContentTags: (contentTags) => set({ contentTags }),
    tab: 'library', face: null, setTab: (tab) => set({ tab }), setFace: (face) => set({ face }),
    selected: [], sort: 'date_desc', favoritesOnly: false, favorites: readFavorites(storage()),
    setSelected: (selected) => set({ selected, face: null }),
    setSort: (sort) => set({ sort }),
    setFavoritesOnly: (favoritesOnly) => set({ favoritesOnly }),
    toggleFavorite: (p) => { const next = toggleFavorite(get().favorites, p); writeFavorites(storage(), next); set({ favorites: next }); },
}));

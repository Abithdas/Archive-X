import { create } from 'zustand'
import type { FaceFilter } from './photos'
import { readFavorites, toggleFavorite, writeFavorites, type Photo, type PhotoSort } from './photos'

const storage = () => { try { return window.localStorage } catch { return null } }

/** Photo page choices; kept for the session like the classic page, favourites persist in this browser. */
export const usePhotoStore = create<{
  tab: 'library' | 'faces'; face: FaceFilter | null; setTab: (t: 'library' | 'faces') => void; setFace: (f: FaceFilter | null) => void
  selected: string[]; sort: PhotoSort; favoritesOnly: boolean; favorites: Set<string>
  setSelected: (ids: string[]) => void; setSort: (s: PhotoSort) => void; setFavoritesOnly: (v: boolean) => void; toggleFavorite: (p: Pick<Photo, 'id'>) => void
}>((set, get) => ({
  tab: 'library', face: null, setTab: (tab) => set({ tab }), setFace: (face) => set({ face }),
  selected: [], sort: 'date_desc', favoritesOnly: false, favorites: readFavorites(storage()),
  setSelected: (selected) => set({ selected, face: null }),
  setSort: (sort) => set({ sort }),
  setFavoritesOnly: (favoritesOnly) => set({ favoritesOnly }),
  toggleFavorite: (p) => { const next = toggleFavorite(get().favorites, p); writeFavorites(storage(), next); set({ favorites: next }) },
}))

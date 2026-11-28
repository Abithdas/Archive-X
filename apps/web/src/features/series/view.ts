import { create } from 'zustand'

export type ScreenView = 'all' | 'movies' | 'series'
/** Which part of Movies & Shows is open (SFW): everything, the movies, or the series. Kept while the app runs, so Back from a movie returns to the movies. */
export type HomeTab = 'home' | 'discover' | 'library'
/** The part of the Home page that is open (For you, Discover, Library), kept the same way. */
export const useHomeTab = create<{ tab: HomeTab; setTab: (t: HomeTab) => void }>((set) => ({ tab: 'home', setTab: (tab) => set({ tab }) }))
export const useScreenView = create<{ view: ScreenView; setView: (v: ScreenView) => void }>((set) => ({ view: 'all', setView: (view) => set({ view }) }))

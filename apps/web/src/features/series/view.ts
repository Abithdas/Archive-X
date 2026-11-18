import { create } from 'zustand'

export type ScreenView = 'all' | 'movies' | 'series'
/** Which part of Movies & Shows is open (SFW): everything, the movies, or the series. Kept while the app runs, so Back from a movie returns to the movies. */
export const useScreenView = create<{ view: ScreenView; setView: (v: ScreenView) => void }>((set) => ({ view: 'all', setView: (view) => set({ view }) }))

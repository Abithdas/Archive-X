import { create } from 'zustand'
import type { EntityKind } from '@/api/types'
import type { GenderFilter } from './filter'
import type { SortKey } from './merge'

/** What the Performers and Studios lists were showing, kept while you open a card and come back (the page used to start over). */
export interface DirectoryView { page: number; gender: GenderFilter; per: number; sort: SortKey }
export const DEFAULT_VIEW: DirectoryView = { page: 1, gender: 'female', per: 0, sort: 'default' }

interface State {
  views: Record<EntityKind, DirectoryView>
  patch: (kind: EntityKind, change: Partial<DirectoryView>) => void
}

export const useDirectoryView = create<State>((set) => ({
  views: { performers: DEFAULT_VIEW, studios: DEFAULT_VIEW },
  patch: (kind, change) => set((s) => ({ views: { ...s.views, [kind]: { ...s.views[kind], ...change } } })),
}))

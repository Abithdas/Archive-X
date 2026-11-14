import { create } from 'zustand'

/** The classic "mapping mode": pick videos on the Library, then edit them together. Keyed `channel:message`. */
export interface Picked { channel_id: string; message_id: number }
export const pickKey = (cid: string, mid: number) => `${cid}:${mid}`

interface SelectionState {
  active: boolean
  picked: Record<string, Picked>
  enter: () => void
  exit: () => void
  toggle: (cid: string, mid: number) => void
  selectMany: (items: ReadonlyArray<{ _cid: string; id: number }>) => void
  clear: () => void
}

export const useSelection = create<SelectionState>((set) => ({
  active: false,
  picked: {},
  enter: () => set({ active: true }),
  exit: () => set({ active: false, picked: {} }),
  toggle: (cid, mid) => set((s) => {
    const k = pickKey(cid, mid)
    const next = { ...s.picked }
    if (next[k]) delete next[k]
    else next[k] = { channel_id: cid, message_id: mid }
    return { picked: next }
  }),
  selectMany: (items) => set((s) => {
    const next = { ...s.picked }
    for (const i of items) next[pickKey(i._cid, i.id)] = { channel_id: i._cid, message_id: i.id }
    return { picked: next }
  }),
  clear: () => set({ picked: {} }),
}))

export const pickedList = (picked: Record<string, Picked>): Picked[] => Object.values(picked)

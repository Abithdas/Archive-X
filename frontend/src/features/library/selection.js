import { create } from 'zustand';
export const pickKey = (cid, mid) => `${cid}:${mid}`;
export const useSelection = create((set) => ({
    active: false,
    picked: {},
    enter: () => set({ active: true }),
    exit: () => set({ active: false, picked: {} }),
    toggle: (cid, mid) => set((s) => {
        const k = pickKey(cid, mid);
        const next = { ...s.picked };
        if (next[k])
            delete next[k];
        else
            next[k] = { channel_id: cid, message_id: mid };
        return { picked: next };
    }),
    selectMany: (items) => set((s) => {
        const next = { ...s.picked };
        for (const i of items)
            next[pickKey(i._cid, i.id)] = { channel_id: i._cid, message_id: i.id };
        return { picked: next };
    }),
    clear: () => set({ picked: {} }),
}));
export const pickedList = (picked) => Object.values(picked);

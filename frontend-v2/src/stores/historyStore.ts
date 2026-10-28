import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface HistoryEntry {
  query: string
  traceId: string
  timestamp: number
  status: string
}

interface HistoryStore {
  entries: HistoryEntry[]
  addEntry: (entry: HistoryEntry) => void
  clear: () => void
}

export const useHistoryStore = create<HistoryStore>()(
  persist(
    (set) => ({
      entries: [],
      addEntry: (entry) =>
        set((state) => ({
          entries: [entry, ...state.entries].slice(0, 100),
        })),
      clear: () => set({ entries: [] }),
    }),
    { name: 'dd-query-history' },
  ),
)

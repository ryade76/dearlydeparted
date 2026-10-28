import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { useStreamStore } from './streamStore'
import { listConversations } from '../api/client'

export interface SessionMessage {
  id: string
  role: 'user' | 'assistant'
  query: string
  answer: string
  traceId: string
  timestamp: number
}

export interface ChatSession {
  id: string
  title: string
  messages: SessionMessage[]
  createdAt: number
  lastActiveAt: number
}

interface BackendTurn {
  role: string
  query: string
  answer: string
  trace_id: string
  timestamp: number
}

interface SessionState {
  sessions: ChatSession[]
  activeSessionId: string | null
  loadingHistory: boolean

  // Actions
  addSession: (id: string, title?: string) => void
  switchSession: (id: string) => void
  deleteSession: (id: string) => void
  renameSession: (id: string, title: string) => void
  addTurn: (query: string, answer: string, traceId: string) => void
  getActiveSession: () => ChatSession | undefined
  setLoadingHistory: (v: boolean) => void
  hydrateFromBackend: () => Promise<void>
  setSessionMessages: (id: string, turns: BackendTurn[]) => void
}

let msgCounter = 0

export const useSessionStore = create<SessionState>()(
  persist(
    (set, get) => ({
      sessions: [],
      activeSessionId: null,
      loadingHistory: false,

      addSession: (id, title) => {
        const session: ChatSession = {
          id,
          title: title || 'New conversation',
          messages: [],
          createdAt: Date.now(),
          lastActiveAt: Date.now(),
        }
        set((s) => ({
          sessions: [session, ...s.sessions],
          activeSessionId: id,
        }))
      },

      switchSession: (id) => {
        useStreamStore.getState().reset()
        set({ activeSessionId: id })
      },

      deleteSession: (id) => {
        set((s) => {
          const remaining = s.sessions.filter((ses) => ses.id !== id)
          const wasActive = s.activeSessionId === id
          return {
            sessions: remaining,
            activeSessionId: wasActive ? (remaining[0]?.id ?? null) : s.activeSessionId,
          }
        })
      },

      renameSession: (id, title) => {
        set((s) => ({
          sessions: s.sessions.map((ses) =>
            ses.id === id ? { ...ses, title } : ses,
          ),
        }))
      },

      addTurn: (query, answer, traceId) => {
        const activeId = get().activeSessionId
        if (!activeId) return

        const msg: SessionMessage = {
          id: `msg_${Date.now()}_${++msgCounter}`,
          role: 'assistant',
          query,
          answer,
          traceId,
          timestamp: Date.now(),
        }

        set((s) => ({
          sessions: s.sessions.map((ses) => {
            if (ses.id !== activeId) return ses
            const updated = {
              ...ses,
              messages: [...ses.messages, msg],
              lastActiveAt: Date.now(),
              // Auto-title from first query
              title: ses.messages.length === 0
                ? query.slice(0, 60) + (query.length > 60 ? '...' : '')
                : ses.title,
            }
            return updated
          }),
        }))
      },

      getActiveSession: () => {
        const state = get()
        return state.sessions.find((s) => s.id === state.activeSessionId)
      },

      setLoadingHistory: (v) => {
        set({ loadingHistory: v })
      },

      hydrateFromBackend: async () => {
        try {
          const { conversations } = await listConversations()
          set((s) => {
            const existingIds = new Set(s.sessions.map((ses) => ses.id))
            // Update existing sessions with backend metadata
            const updated = s.sessions.map((ses) => {
              const bc = conversations.find((c) => c.id === ses.id)
              if (bc) return { ...ses, title: bc.title, lastActiveAt: bc.last_active_at * 1000 }
              return ses
            })
            // Add backend-only sessions (not yet in localStorage)
            const newSessions = conversations
              .filter((c) => !existingIds.has(c.id))
              .map((c): ChatSession => ({
                id: c.id,
                title: c.title,
                messages: [],
                createdAt: c.created_at * 1000,
                lastActiveAt: c.last_active_at * 1000,
              }))
            const merged = [...updated, ...newSessions]
            merged.sort((a, b) => b.lastActiveAt - a.lastActiveAt)
            return { sessions: merged }
          })
        } catch {
          // Backend unreachable — keep localStorage sessions as-is
        }
      },

      setSessionMessages: (id, turns) => {
        set((s) => ({
          sessions: s.sessions.map((ses) => {
            if (ses.id !== id) return ses
            return {
              ...ses,
              messages: turns.map((t, i): SessionMessage => ({
                id: `msg_${Math.floor(t.timestamp * 1000)}_${i}`,
                role: t.role === 'user' ? 'user' : 'assistant',
                query: t.query,
                answer: t.answer,
                traceId: t.trace_id,
                timestamp: t.timestamp * 1000,
              })),
            }
          }),
        }))
      },
    }),
    {
      name: 'dd-chat-sessions',
      version: 1,
    },
  ),
)

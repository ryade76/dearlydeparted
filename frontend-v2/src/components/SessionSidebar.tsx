import { useCallback, useEffect } from 'react'
import { useSessionStore, type ChatSession } from '../stores/sessionStore'
import { createConversation, deleteConversation, getConversation } from '../api/client'
import { truncate } from '../lib/utils'

function timeAgo(ts: number): string {
  const diff = Date.now() - ts
  if (diff < 60_000) return 'just now'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`
  return `${Math.floor(diff / 86_400_000)}d ago`
}

export function SessionSidebar() {
  const sessions = useSessionStore((s) => s.sessions)
  const activeSessionId = useSessionStore((s) => s.activeSessionId)
  const addSession = useSessionStore((s) => s.addSession)
  const switchSession = useSessionStore((s) => s.switchSession)
  const deleteSessionLocal = useSessionStore((s) => s.deleteSession)
  const hydrateFromBackend = useSessionStore((s) => s.hydrateFromBackend)
  const setSessionMessages = useSessionStore((s) => s.setSessionMessages)
  const setLoadingHistory = useSessionStore((s) => s.setLoadingHistory)

  // Hydrate session list from backend on mount
  useEffect(() => { hydrateFromBackend() }, [hydrateFromBackend])

  const handleNewChat = useCallback(async () => {
    try {
      const resp = await createConversation()
      addSession(resp.conversation_id, resp.title)
    } catch {
      // Fallback to local-only session
      const localId = `local_${Date.now()}`
      addSession(localId)
    }
  }, [addSession])

  const handleDelete = useCallback(
    (e: React.MouseEvent, session: ChatSession) => {
      e.stopPropagation()
      deleteSessionLocal(session.id)
      deleteConversation(session.id).catch(() => {})
    },
    [deleteSessionLocal],
  )

  const handleSwitch = useCallback(async (id: string) => {
    if (id === activeSessionId) return
    switchSession(id)
    if (id.startsWith('local_')) return
    setLoadingHistory(true)
    try {
      const detail = await getConversation(id)
      // Stale-fetch guard: only apply if user hasn't switched again
      if (useSessionStore.getState().activeSessionId === id) {
        setSessionMessages(id, detail.turns)
      }
    } catch {
      // Cached localStorage messages show as fallback
    } finally {
      if (useSessionStore.getState().activeSessionId === id) {
        setLoadingHistory(false)
      }
    }
  }, [activeSessionId, switchSession, setSessionMessages, setLoadingHistory])

  return (
    <div className="flex flex-col h-full bg-slate-50 border-r border-slate-200">
      {/* Header + New Chat */}
      <div className="p-3 border-b border-slate-200">
        <button
          onClick={handleNewChat}
          className="w-full px-3 py-2 text-xs font-semibold bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors"
        >
          + New Chat
        </button>
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto">
        {sessions.length === 0 && (
          <div className="p-4 text-xs text-slate-400 text-center">
            No conversations yet.
            <br />
            Start a new chat!
          </div>
        )}
        {sessions.map((session) => (
          <div
            key={session.id}
            onClick={() => handleSwitch(session.id)}
            className={`group flex items-start gap-2 px-3 py-2.5 cursor-pointer border-b border-slate-100 transition-colors ${
              session.id === activeSessionId
                ? 'bg-blue-50 border-l-2 border-l-blue-600'
                : 'hover:bg-slate-100 border-l-2 border-l-transparent'
            }`}
          >
            <div className="flex-1 min-w-0">
              <div className="text-xs font-medium text-slate-700 truncate">
                {truncate(session.title, 40)}
              </div>
              <div className="text-[10px] text-slate-400 mt-0.5">
                {session.messages.length} turn{session.messages.length !== 1 ? 's' : ''}
                {' \u00b7 '}
                {timeAgo(session.lastActiveAt)}
              </div>
            </div>
            <button
              onClick={(e) => handleDelete(e, session)}
              className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-red-500 text-xs px-1 transition-opacity"
              title="Delete conversation"
            >
              \u2715
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

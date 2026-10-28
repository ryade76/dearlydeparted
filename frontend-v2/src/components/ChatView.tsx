import { useState, useEffect, useCallback, useRef, type FormEvent } from 'react'
import { useAgentStream } from '../hooks/useAgentStream'
import { useStreamStore } from '../stores/streamStore'
import { useSessionStore } from '../stores/sessionStore'
import { useHistoryStore } from '../stores/historyStore'
import { createConversation } from '../api/client'
import ReactMarkdown from 'react-markdown'
import { StreamingMessage } from './StreamingMessage'
import { CitationCard } from './CitationCard'
import { DocumentViewerModal } from './DocumentViewerModal'
import { QueryHistory } from './QueryHistory'
import type { Citation } from '../api/types'

function cleanAnswerForDisplay(text: string): string {
  // Strip inline citation markers [hex_id:N] (8+ hex chars)
  let cleaned = text.replace(/\[[a-f0-9]{8,}[^\]]*\]/g, '')
  // Strip Sources/References section at end (not useful without citation cards)
  cleaned = cleaned.replace(/\n+#{1,4}\s*Sources\b[\s\S]*$/i, '')
  return cleaned.trim()
}

export function ChatView() {
  const [query, setQuery] = useState('')
  const [submittedQuery, setSubmittedQuery] = useState('')
  const [viewingCitation, setViewingCitation] = useState<Citation | null>(null)
  const { ask, cancel } = useAgentStream()
  const { status, citations, errorMessage, traceId, finalAnswer, verificationResult } = useStreamStore()
  const activeSessionId = useSessionStore((s) => s.activeSessionId)
  const activeSession = useSessionStore((s) => s.sessions.find((ses) => ses.id === s.activeSessionId))
  const addSession = useSessionStore((s) => s.addSession)
  const addTurn = useSessionStore((s) => s.addTurn)
  const loadingHistory = useSessionStore((s) => s.loadingHistory)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll when new content arrives
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [status, finalAnswer])

  // Clear stale query when switching sessions
  useEffect(() => {
    setSubmittedQuery('')
  }, [activeSessionId])

  // Save completed turn to session
  useEffect(() => {
    if (status === 'complete' && finalAnswer && submittedQuery && traceId) {
      addTurn(submittedQuery, finalAnswer, traceId)
    }
  }, [status, finalAnswer, submittedQuery, traceId, addTurn])

  // Ensure a session exists before sending
  const ensureSession = useCallback(async (): Promise<string> => {
    if (activeSessionId) return activeSessionId
    try {
      const resp = await createConversation()
      addSession(resp.conversation_id, resp.title)
      return resp.conversation_id
    } catch {
      const localId = `local_${Date.now()}`
      addSession(localId)
      return localId
    }
  }, [activeSessionId, addSession])

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!query.trim() || status === 'streaming' || status === 'connecting') return
    const q = query.trim()
    setSubmittedQuery(q)

    const sessionId = await ensureSession()

    ask({ query: q, conversation_id: sessionId })

    useHistoryStore.getState().addEntry({
      query: q,
      traceId: '',
      timestamp: Date.now(),
      status: 'pending',
    })
  }

  // Past turns from the active session
  const pastTurns = activeSession?.messages ?? []

  return (
    <div className="flex flex-col h-full">
      {/* Messages area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-4">
        {status === 'idle' && pastTurns.length === 0 && !loadingHistory && (
          <div className="flex flex-col items-center justify-center h-full gap-6">
            <div className="text-center text-slate-400">
              <div className="text-4xl mb-3">&#x1F50D;</div>
              <h2 className="text-lg font-semibold text-slate-600">Ask Dearly Departed</h2>
              <p className="text-sm mt-1">
                Query the captured knowledge base with full-context retrieval and citation verification
              </p>
            </div>
            <div className="w-full max-w-md">
              <QueryHistory />
            </div>
          </div>
        )}

        {loadingHistory && (
          <div className="flex items-center justify-center py-8">
            <div className="animate-spin w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full" />
            <span className="ml-2 text-sm text-slate-400">Loading conversation...</span>
          </div>
        )}

        {/* Past conversation turns */}
        {pastTurns.map((turn) => (
          <div key={turn.id} className="space-y-2">
            <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-3">
              <p className="text-sm text-blue-900 font-medium">{turn.query}</p>
            </div>
            <div className="bg-white border border-slate-200 rounded-lg px-4 py-3 shadow-sm">
              <div className="prose prose-slate prose-sm max-w-none">
                <ReactMarkdown>{cleanAnswerForDisplay(turn.answer)}</ReactMarkdown>
              </div>
              {turn.traceId && (
                <div className="text-[10px] text-slate-400 mt-2">Trace: {turn.traceId}</div>
              )}
            </div>
          </div>
        ))}

        {/* Current query being processed */}
        {status === 'connecting' && (
          <div className="space-y-2">
            <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-3">
              <p className="text-sm text-blue-900 font-medium">{submittedQuery}</p>
            </div>
            <div className="flex items-center justify-center py-8">
              <div className="text-center text-slate-400">
                <div className="animate-spin w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full mx-auto mb-3" />
                <p className="text-sm">Connecting to agent...</p>
              </div>
            </div>
          </div>
        )}

        {(status === 'streaming' || status === 'complete') && (
          <div className="space-y-4">
            {/* Query */}
            <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-3">
              <p className="text-sm text-blue-900 font-medium">{submittedQuery}</p>
            </div>

            {/* Streaming answer */}
            <div className="bg-white border border-slate-200 rounded-lg px-4 py-3 shadow-sm">
              <StreamingMessage />
            </div>

            {/* Citations */}
            {citations.length > 0 && (
              <div className="space-y-2">
                <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                  Citations ({verificationResult?.verified ?? citations.length}/{verificationResult?.total ?? citations.length} verified)
                </h3>
                <div className="grid gap-2">
                  {citations
                    .filter((c) => c.verified)
                    .map((c) => (
                      <CitationCard key={c.item_id} citation={c} onClick={setViewingCitation} />
                    ))}
                </div>
              </div>
            )}

            {/* Trace ID */}
            {traceId && status === 'complete' && (
              <div className="text-[10px] text-slate-400">
                Trace: {traceId}
              </div>
            )}
          </div>
        )}

        {status === 'error' && (
          <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700">
            {errorMessage || 'An error occurred'}
          </div>
        )}
      </div>

      {/* Input area */}
      <div className="border-t border-slate-200 bg-white p-4">
        <form onSubmit={handleSubmit} className="flex gap-2">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask a question about the captured knowledge..."
            className="flex-1 px-4 py-2.5 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            disabled={status === 'streaming' || status === 'connecting'}
          />
          {(status === 'streaming' || status === 'connecting') ? (
            <button
              type="button"
              onClick={cancel}
              className="px-4 py-2.5 bg-red-600 text-white rounded-lg text-sm font-medium hover:bg-red-700 transition-colors"
            >
              Cancel
            </button>
          ) : (
            <button
              type="submit"
              disabled={!query.trim()}
              className="px-6 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              Ask
            </button>
          )}
        </form>
      </div>

      <DocumentViewerModal
        citation={viewingCitation}
        onClose={() => setViewingCitation(null)}
      />
    </div>
  )
}

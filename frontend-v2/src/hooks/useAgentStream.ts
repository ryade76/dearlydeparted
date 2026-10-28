import { useCallback, useRef } from 'react'
import { streamAsk, type AskRequest } from '../api/client'
import { useStreamStore } from '../stores/streamStore'
import type {
  SessionStartEvent,
  StateTransitionEvent,
  ThinkingEvent,
  ToolCallStartEvent,
  ToolCallResultEvent,
  ToolCallErrorEvent,
  ContextAssembledEvent,
  GenerationStartEvent,
  GenerationTokenEvent,
  GenerationCompleteEvent,
  AnswerCompleteEvent,
  ErrorEvent,
  CitationVerifiedEvent,
  VerificationResultEvent,
} from '../api/types'

export function useAgentStream() {
  const store = useStreamStore()
  const abortRef = useRef<AbortController | null>(null)

  const ask = useCallback(async (request: AskRequest) => {
    // Cancel any existing stream
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    store.reset()
    store.setStatus('connecting')

    try {
      await streamAsk(
        request,
        (eventType: string, data: unknown) => {
          const s = useStreamStore.getState()

          switch (eventType) {
            case 'session_start': {
              const d = data as SessionStartEvent
              s.setTraceId(d.trace_id)
              s.setStatus('streaming')
              break
            }
            case 'state_transition': {
              const d = data as StateTransitionEvent
              s.addStateTransition(d.from_state, d.to_state, d.duration_ms)
              break
            }
            case 'thinking': {
              const d = data as ThinkingEvent
              s.addThinking(d)
              break
            }
            case 'tool_call_start': {
              const d = data as ToolCallStartEvent
              s.addToolCallStart(d.tool_call_id, d.tool_name, d.arguments)
              break
            }
            case 'tool_call_result': {
              const d = data as ToolCallResultEvent
              s.updateToolCallResult(d.tool_call_id, d.result_summary, d.duration_ms)
              break
            }
            case 'tool_call_error': {
              const d = data as ToolCallErrorEvent
              s.updateToolCallError(d.tool_call_id, d.error)
              break
            }
            case 'context_assembled': {
              const d = data as ContextAssembledEvent
              s.setContextInfo(d)
              break
            }
            case 'generation_start': {
              const d = data as GenerationStartEvent
              s.setGenerating(d.model, d.context_tokens)
              break
            }
            case 'generation_token': {
              const d = data as GenerationTokenEvent
              s.appendToken(d.token)
              break
            }
            case 'generation_complete': {
              const d = data as GenerationCompleteEvent
              s.setGenerationComplete(d.total_tokens, d.finish_reason)
              break
            }
            case 'citation_verified': {
              const d = data as CitationVerifiedEvent
              s.addCitationVerified({
                index: d.citation_index,
                item_id: d.item_id,
                path: d.path,
                verified: d.verified,
                verification_method: d.method,
                verification_score: d.confidence,
                text_excerpt: '',
                domain: '',
                bucket: '',
                date: '',
              })
              break
            }
            case 'verification_result': {
              const d = data as VerificationResultEvent
              s.setVerificationResult(d.total_citations, d.verified_count, d.pass_rate)
              break
            }
            case 'answer_complete': {
              const d = data as AnswerCompleteEvent
              s.setFinalAnswer(d.answer, d.citations, d.timing, d.diagnostics, d.model || '')
              break
            }
            case 'error': {
              const d = data as ErrorEvent
              s.setError(`${d.state}: ${d.error}`)
              break
            }
            case 'done':
              if (s.status !== 'complete') {
                s.setStatus('complete')
              }
              break
          }
        },
        controller.signal,
      )
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return
      store.setError(String(err))
    }
  }, [store])

  const cancel = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  return { ask, cancel }
}

import { create } from 'zustand'
import type {
  AgentState,
  Citation,
  ToolCall,
  ThinkingEvent,
  ContextAssembledEvent,
} from '../api/types'

interface StreamState {
  // Connection
  status: 'idle' | 'connecting' | 'streaming' | 'complete' | 'error'
  traceId: string

  // Agent state
  currentState: AgentState
  stateHistory: { state: string; duration_ms: number }[]

  // Thinking
  thinkingMessages: ThinkingEvent[]

  // Tool calls
  toolCalls: ToolCall[]

  // Generation
  tokens: string
  isGenerating: boolean
  model: string
  contextTokens: number
  totalTokens: number
  finishReason: string
  finalModel: string

  // Context
  contextInfo: ContextAssembledEvent | null

  // Citations
  citations: Citation[]
  verificationResult: { total: number; verified: number; passRate: number } | null

  // Final
  finalAnswer: string
  timing: Record<string, number>
  diagnostics: Record<string, unknown>

  // Error
  errorMessage: string

  // Actions
  reset: () => void
  setStatus: (s: StreamState['status']) => void
  setTraceId: (id: string) => void
  addStateTransition: (from: string, to: string, duration_ms: number) => void
  addThinking: (t: ThinkingEvent) => void
  addToolCallStart: (id: string, name: string, args: Record<string, unknown>) => void
  updateToolCallResult: (id: string, summary: Record<string, unknown>, duration_ms: number) => void
  updateToolCallError: (id: string, error: string) => void
  setContextInfo: (info: ContextAssembledEvent) => void
  setGenerating: (model: string, tokens: number) => void
  appendToken: (token: string) => void
  setGenerationComplete: (totalTokens: number, finishReason: string) => void
  addCitationVerified: (c: Citation) => void
  setVerificationResult: (total: number, verified: number, passRate: number) => void
  setFinalAnswer: (answer: string, citations: Citation[], timing: Record<string, number>, diagnostics: Record<string, unknown>, model?: string) => void
  setError: (msg: string) => void
}

const initialState = {
  status: 'idle' as const,
  traceId: '',
  currentState: 'idle' as AgentState,
  stateHistory: [],
  thinkingMessages: [],
  toolCalls: [],
  tokens: '',
  isGenerating: false,
  model: '',
  contextTokens: 0,
  totalTokens: 0,
  finishReason: '',
  finalModel: '',
  contextInfo: null,
  citations: [],
  verificationResult: null,
  finalAnswer: '',
  timing: {},
  diagnostics: {},
  errorMessage: '',
}

export const useStreamStore = create<StreamState>((set) => ({
  ...initialState,

  reset: () => set(initialState),
  setStatus: (status) => set({ status }),
  setTraceId: (traceId) => set({ traceId }),
  addStateTransition: (from, to, duration_ms) =>
    set((s) => ({ stateHistory: [...s.stateHistory, { state: from, duration_ms }], currentState: to as AgentState })),
  addThinking: (t) => set((s) => ({ thinkingMessages: [...s.thinkingMessages, t] })),
  addToolCallStart: (id, name, args) =>
    set((s) => ({ toolCalls: [...s.toolCalls, { id, name, arguments: args, status: 'running' }] })),
  updateToolCallResult: (id, summary, duration_ms) =>
    set((s) => ({
      toolCalls: s.toolCalls.map((tc) =>
        tc.id === id ? { ...tc, status: 'complete' as const, result_summary: summary, duration_ms } : tc,
      ),
    })),
  updateToolCallError: (id, error) =>
    set((s) => ({
      toolCalls: s.toolCalls.map((tc) =>
        tc.id === id ? { ...tc, status: 'error' as const, error } : tc,
      ),
    })),
  setContextInfo: (contextInfo) => set({ contextInfo }),
  setGenerating: (model, contextTokens) => set({ isGenerating: true, model, contextTokens, tokens: '' }),
  appendToken: (token) => set((s) => ({ tokens: s.tokens + token })),
  setGenerationComplete: (totalTokens, finishReason) => set({ isGenerating: false, totalTokens, finishReason }),
  addCitationVerified: (c) => set((s) => ({ citations: [...s.citations, c] })),
  setVerificationResult: (total, verified, passRate) =>
    set({ verificationResult: { total, verified, passRate } }),
  setFinalAnswer: (finalAnswer, citations, timing, diagnostics, model) =>
    set({ finalAnswer, citations, timing, diagnostics, status: 'complete', ...(model ? { finalModel: model } : {}) }),
  setError: (errorMessage) => set({ errorMessage, status: 'error' }),
}))

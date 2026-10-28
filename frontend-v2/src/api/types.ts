// SSE event types matching the 15 server event types

export interface SessionStartEvent {
  trace_id: string
  query: string
  timestamp: string
}

export interface StateTransitionEvent {
  from_state: string
  to_state: string
  duration_ms: number
  context_summary: Record<string, unknown>
}

export interface ThinkingEvent {
  content: string
  structured: {
    intent?: string
    complexity?: string
    strategy?: string
    plan_steps?: { step: number; tool: string; description: string }[]
    taxonomy_hints?: Record<string, unknown>
    temporal_range?: Record<string, unknown>
  }
}

export interface ToolCallStartEvent {
  tool_call_id: string
  tool_name: string
  arguments: Record<string, unknown>
  reason: string
}

export interface ToolCallResultEvent {
  tool_call_id: string
  tool_name: string
  duration_ms: number
  result_summary: {
    hit_count?: number
    top_sources?: string[]
    [key: string]: unknown
  }
}

export interface ToolCallErrorEvent {
  tool_call_id: string
  error: string
}

export interface ContextAssembledEvent {
  token_count: number
  section_breakdown: Record<string, number>
}

export interface GenerationStartEvent {
  model: string
  context_tokens: number
}

export interface GenerationTokenEvent {
  token: string
}

export interface GenerationCompleteEvent {
  total_tokens: number
  finish_reason: string
}

export interface CitationVerifiedEvent {
  citation_index: number
  item_id: string
  path: string
  verified: boolean
  method: string
  confidence: number
}

export interface VerificationResultEvent {
  total_citations: number
  verified_count: number
  pass_rate: number
}

export interface AnswerCompleteEvent {
  answer: string
  citations: Citation[]
  timing: Record<string, number>
  diagnostics: Record<string, unknown>
  model?: string
}

export interface ErrorEvent {
  error: string
  state: string
  recoverable: boolean
}

export interface Citation {
  index: number
  item_id: string
  path: string
  text_excerpt: string
  verified: boolean
  verification_method: string
  verification_score: number
  domain: string
  bucket: string
  date: string
  doc_type?: string
  file_url?: string
}

export interface ToolCall {
  id: string
  name: string
  arguments: Record<string, unknown>
  status: 'pending' | 'running' | 'complete' | 'error'
  result_summary?: Record<string, unknown>
  duration_ms?: number
  error?: string
}

export type AgentState =
  | 'idle'
  | 'classify'
  | 'plan'
  | 'execute'
  | 'react'
  | 'synthesize'
  | 'generate'
  | 'verify'
  | 'emit_final'
  | 'done'
  | 'error'

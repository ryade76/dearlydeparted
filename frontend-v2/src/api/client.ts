// SSE streaming client for v2 agent API

export interface AskRequest {
  query: string
  conversation_id?: string
  domain?: string
  mode?: string
  top_k?: number
  strategy?: string
  max_context_tokens?: number
  max_completion_tokens?: number
  temperature?: number
  include_thinking?: boolean
  include_tool_details?: boolean
}

export type SSEEventHandler = (eventType: string, data: unknown) => void

export async function streamAsk(
  request: AskRequest,
  onEvent: SSEEventHandler,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch('/api/v2/ask/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
  })

  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`)
  }

  const reader = response.body?.getReader()
  if (!reader) throw new Error('No response body')

  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })

    // Parse SSE events from buffer
    const lines = buffer.split('\n')
    buffer = lines.pop() || '' // Keep incomplete line in buffer

    let currentEvent = ''
    let currentData = ''

    for (const line of lines) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7).trim()
      } else if (line.startsWith('data: ')) {
        currentData = line.slice(6)
      } else if (line === '' && currentEvent && currentData) {
        try {
          const data = JSON.parse(currentData)
          onEvent(currentEvent, data)
        } catch {
          // Skip malformed JSON
        }
        currentEvent = ''
        currentData = ''
      }
    }
  }
}

export async function fetchHealth(): Promise<Record<string, unknown>> {
  const resp = await fetch('/api/v2/health')
  return resp.json()
}

// ---- Conversation CRUD ----

export interface ConversationSummary {
  id: string
  title: string
  turn_count: number
  created_at: number
  last_active_at: number
}

export async function createConversation(title?: string): Promise<{ conversation_id: string; title: string }> {
  const resp = await fetch('/api/v2/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title: title || '' }),
  })
  if (!resp.ok) throw new Error(`Failed to create conversation: ${resp.status}`)
  return resp.json()
}

export async function deleteConversation(id: string): Promise<void> {
  await fetch(`/api/v2/conversations/${id}`, { method: 'DELETE' })
}

export async function listConversations(limit = 50): Promise<{ conversations: ConversationSummary[]; count: number }> {
  const resp = await fetch(`/api/v2/conversations?limit=${limit}`)
  if (!resp.ok) throw new Error(`Failed to list conversations: ${resp.status}`)
  return resp.json()
}

export interface ConversationDetail {
  id: string
  title: string
  created_at: number
  last_active_at: number
  turns: { role: string; query: string; answer: string; trace_id: string; timestamp: number }[]
}

export async function getConversation(id: string): Promise<ConversationDetail> {
  const resp = await fetch(`/api/v2/conversations/${id}`)
  if (!resp.ok) throw new Error(`Failed to get conversation: ${resp.status}`)
  return resp.json()
}


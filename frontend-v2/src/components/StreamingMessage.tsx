import { type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import { useStreamStore } from '../stores/streamStore'
import { CitationBadge } from './CitationBadge'

function renderCitationBadges(text: string, citations: { item_id: string; verified: boolean }[]): ReactNode {
  const citationMap = new Map(citations.map((c, i) => [c.item_id, { index: i + 1, verified: c.verified }]))
  const parts = text.split(/(\[[^\]]+\])/)
  return parts.map((part, i) => {
    const match = part.match(/^\[([^\]]+)\]$/)
    if (match) {
      const cid = match[1]
      const info = citationMap.get(cid)
      if (info) {
        return <CitationBadge key={i} index={info.index} verified={info.verified} />
      }
    }
    return part
  })
}

export function StreamingMessage() {
  const { tokens, finalAnswer, isGenerating, status, citations } = useStreamStore()
  const content = finalAnswer || tokens

  if (!content && status !== 'streaming') return null

  return (
    <div className="prose prose-slate prose-sm max-w-none">
      <ReactMarkdown
        components={{
          // Style citation markers
          a: ({ children, ...props }) => (
            <a {...props} className="text-blue-600 hover:text-blue-800 no-underline font-medium">
              {children}
            </a>
          ),
          p: ({ children }) => {
            // Render citation badges in paragraph text
            if (typeof children === 'string' && citations.length > 0) {
              return <p className="mb-2 leading-relaxed">{renderCitationBadges(children, citations)}</p>
            }
            return <p className="mb-2 leading-relaxed">{children}</p>
          },
          h1: ({ children }) => <h1 className="text-lg font-bold mt-4 mb-2">{children}</h1>,
          h2: ({ children }) => <h2 className="text-base font-bold mt-3 mb-2">{children}</h2>,
          h3: ({ children }) => <h3 className="text-sm font-bold mt-3 mb-1">{children}</h3>,
          ul: ({ children }) => <ul className="list-disc pl-5 mb-2 space-y-1">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal pl-5 mb-2 space-y-1">{children}</ol>,
          li: ({ children }) => <li className="text-sm">{children}</li>,
          code: ({ children, className }) => {
            const isBlock = className?.includes('language-')
            if (isBlock) {
              return (
                <pre className="bg-slate-900 text-slate-100 rounded-md p-3 overflow-x-auto text-xs">
                  <code>{children}</code>
                </pre>
              )
            }
            return (
              <code className="bg-slate-100 text-slate-800 px-1 py-0.5 rounded text-xs font-mono">
                {children}
              </code>
            )
          },
        }}
      >
        {content}
      </ReactMarkdown>
      {isGenerating && (
        <span className="inline-block w-2 h-4 bg-blue-500 animate-pulse ml-0.5" />
      )}
    </div>
  )
}

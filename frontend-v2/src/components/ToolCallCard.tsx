import { cn } from '../lib/cn'
import type { ToolCall } from '../api/types'

interface ToolCallCardProps {
  toolCall: ToolCall
}

export function ToolCallCard({ toolCall: tc }: ToolCallCardProps) {
  return (
    <div
      className={cn(
        'rounded border px-2.5 py-2 text-xs',
        tc.status === 'running' && 'border-blue-300 bg-blue-50',
        tc.status === 'complete' && 'border-emerald-200 bg-emerald-50',
        tc.status === 'error' && 'border-red-200 bg-red-50',
      )}
    >
      <div className="flex items-center justify-between">
        <span className="font-semibold text-slate-800">{tc.name}</span>
        <div className="flex items-center gap-2">
          {tc.duration_ms !== undefined && (
            <span className="text-[10px] text-slate-500">
              {tc.duration_ms < 1000
                ? `${Math.round(tc.duration_ms)}ms`
                : `${(tc.duration_ms / 1000).toFixed(1)}s`}
            </span>
          )}
          <span
            className={cn(
              'inline-block w-2 h-2 rounded-full',
              tc.status === 'running' && 'bg-blue-500 animate-pulse',
              tc.status === 'complete' && 'bg-emerald-500',
              tc.status === 'error' && 'bg-red-500',
            )}
          />
        </div>
      </div>
      {/* Arguments */}
      <div className="mt-1 text-[10px] text-slate-500 font-mono truncate">
        {Object.entries(tc.arguments)
          .filter(([_, v]) => v !== '' && v !== undefined)
          .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
          .join(', ')}
      </div>
      {/* Result summary */}
      {tc.result_summary && (
        <div className="mt-1 text-[10px] text-emerald-700">
          {tc.result_summary.hit_count != null ? (
            <span>{String(tc.result_summary.hit_count)} hits</span>
          ) : null}
          {Array.isArray(tc.result_summary.top_sources) && tc.result_summary.top_sources.length > 0 ? (
            <span className="ml-2 text-slate-500">
              top: {(tc.result_summary.top_sources as string[]).slice(0, 2).join(', ')}
            </span>
          ) : null}
        </div>
      )}
      {tc.error && (
        <div className="mt-1 text-[10px] text-red-600">{tc.error}</div>
      )}
    </div>
  )
}

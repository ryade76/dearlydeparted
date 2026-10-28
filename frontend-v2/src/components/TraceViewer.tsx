import { useStreamStore } from '../stores/streamStore'
import { formatDuration } from '../lib/utils'

export function TraceViewer() {
  const { traceId, timing, diagnostics, toolCalls, model, totalTokens, finishReason } = useStreamStore()

  return (
    <div className="space-y-4">
      {/* Trace metadata */}
      {traceId && (
        <div className="space-y-1">
          <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Trace</h3>
          <div className="text-xs font-mono text-slate-600 bg-slate-50 p-2 rounded border border-slate-200 break-all">
            {traceId}
          </div>
          {model && <div className="text-[10px] text-slate-500">Model: {model}</div>}
          {totalTokens > 0 && <div className="text-[10px] text-slate-500">Output tokens: {totalTokens}</div>}
          {finishReason && <div className="text-[10px] text-slate-500">Finish: {finishReason}</div>}
        </div>
      )}

      {/* State timing waterfall */}
      <div className="space-y-1">
        <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">State Timing</h3>
        {Object.entries(timing).length > 0 ? (
          <div className="space-y-1">
            {(() => {
              const maxMs = Math.max(...Object.values(timing), 1)
              return Object.entries(timing).map(([state, ms]) => (
                <div key={state} className="flex items-center gap-2 text-xs">
                  <span className="w-20 text-slate-600 text-right truncate">{state}</span>
                  <div className="flex-1 bg-slate-100 rounded-full h-3 overflow-hidden">
                    <div
                      className="bg-blue-500 h-full rounded-full transition-all"
                      style={{ width: `${Math.max((ms / maxMs) * 100, 2)}%` }}
                    />
                  </div>
                  <span className="text-slate-500 font-mono w-16 text-right">{formatDuration(ms)}</span>
                </div>
              ))
            })()}
            <div className="flex justify-between text-xs font-semibold border-t border-slate-200 pt-1 mt-1">
              <span className="text-slate-700">Total</span>
              <span className="text-slate-700 font-mono">
                {formatDuration(Object.values(timing).reduce((a, b) => a + b, 0))}
              </span>
            </div>
          </div>
        ) : (
          <p className="text-xs text-slate-400">Timing data will appear after completion.</p>
        )}
      </div>

      {/* Tool call tree */}
      {toolCalls.length > 0 && (
        <div className="space-y-1">
          <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
            Tool Calls ({toolCalls.length})
          </h3>
          <div className="space-y-0.5">
            {toolCalls.map((tc) => (
              <div key={tc.id} className="flex justify-between text-xs py-0.5">
                <span className="text-slate-600 truncate">{tc.name}</span>
                <span className="text-slate-500 font-mono">
                  {tc.duration_ms !== undefined ? formatDuration(tc.duration_ms) : '...'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Diagnostics */}
      {Object.keys(diagnostics).length > 0 && (
        <div className="space-y-1">
          <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Diagnostics</h3>
          <pre className="text-[10px] text-slate-600 bg-slate-50 p-2 rounded border border-slate-200 overflow-x-auto">
            {JSON.stringify(diagnostics, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}

import { useHistoryStore } from '../stores/historyStore'
import { truncate } from '../lib/utils'

export function QueryHistory() {
  const { entries, clear } = useHistoryStore()

  if (entries.length === 0) return null

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Recent Queries</h3>
        <button
          onClick={clear}
          className="text-[10px] text-slate-400 hover:text-slate-600 transition-colors"
        >
          Clear
        </button>
      </div>
      <div className="space-y-1">
        {entries.slice(0, 10).map((entry, i) => (
          <div
            key={`${entry.traceId}-${i}`}
            className="flex items-start justify-between gap-2 py-1.5 px-2 rounded text-xs hover:bg-slate-50 transition-colors"
          >
            <div className="flex-1 min-w-0">
              <p className="text-slate-700 truncate">{truncate(entry.query, 60)}</p>
              <p className="text-[10px] text-slate-400 mt-0.5">
                {new Date(entry.timestamp).toLocaleTimeString()}
              </p>
            </div>
            <button
              onClick={() => navigator.clipboard.writeText(entry.traceId)}
              className="text-[10px] text-slate-400 hover:text-blue-600 shrink-0"
              title="Copy trace ID"
            >
              {entry.traceId.slice(0, 8)}
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

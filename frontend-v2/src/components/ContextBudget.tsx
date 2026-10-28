import { useStreamStore } from '../stores/streamStore'

const SECTION_COLORS: Record<string, string> = {
  taxonomy: 'bg-violet-500',
  entity_profiles: 'bg-blue-500',
  graph_traversal: 'bg-cyan-500',
  graphrag_artifacts: 'bg-amber-500',
  text_chunks: 'bg-emerald-500',
  temporal: 'bg-rose-500',
}

const SECTION_LABELS: Record<string, string> = {
  taxonomy: 'Taxonomy',
  entity_profiles: 'Entities',
  graph_traversal: 'Graph',
  graphrag_artifacts: 'Artifacts',
  text_chunks: 'Chunks',
  temporal: 'Temporal',
}

export function ContextBudget() {
  const { contextInfo } = useStreamStore()

  if (!contextInfo) return null

  const total = contextInfo.token_count
  const sections = Object.entries(contextInfo.section_breakdown)
    .filter(([_, v]) => v > 0)
    .sort((a, b) => b[1] - a[1])

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
        Context Budget
        <span className="ml-2 font-normal text-slate-400">
          {total < 1000 ? total : `${(total / 1000).toFixed(0)}K`} tokens
        </span>
      </h3>
      {/* Stacked bar */}
      <div className="w-full h-3 bg-slate-200 rounded-full overflow-hidden flex">
        {sections.map(([name, count]) => (
          <div
            key={name}
            className={`${SECTION_COLORS[name] || 'bg-slate-400'} transition-all`}
            style={{ width: `${(count / total) * 100}%` }}
            title={`${SECTION_LABELS[name] || name}: ${count.toLocaleString()} tokens`}
          />
        ))}
      </div>
      {/* Legend */}
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        {sections.map(([name, count]) => (
          <div key={name} className="flex items-center gap-1 text-[10px] text-slate-600">
            <span className={`w-2 h-2 rounded-full ${SECTION_COLORS[name] || 'bg-slate-400'}`} />
            {SECTION_LABELS[name] || name}: {count < 1000 ? count : `${(count / 1000).toFixed(0)}K`}
          </div>
        ))}
      </div>
    </div>
  )
}

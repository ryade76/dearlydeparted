import { useStreamStore } from '../stores/streamStore'
import { ToolCallCard } from './ToolCallCard'

export function ToolCallTimeline() {
  const { toolCalls } = useStreamStore()

  if (toolCalls.length === 0) return null

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Tool Calls</h3>
      <div className="space-y-1.5">
        {toolCalls.map((tc) => (
          <ToolCallCard key={tc.id} toolCall={tc} />
        ))}
      </div>
    </div>
  )
}

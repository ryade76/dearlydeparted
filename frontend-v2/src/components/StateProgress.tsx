import { cn } from '../lib/cn'
import { useStreamStore } from '../stores/streamStore'

const REACT_STATES = ['classify', 'react', 'synthesize', 'generate', 'verify', 'emit_final'] as const
const PLAN_EXECUTE_STATES = ['classify', 'plan', 'execute', 'synthesize', 'generate', 'verify', 'emit_final'] as const

const STATE_LABELS: Record<string, string> = {
  classify: 'Classify',
  plan: 'Plan',
  react: 'React',
  execute: 'Execute',
  synthesize: 'Synthesize',
  generate: 'Generate',
  verify: 'Verify',
  emit_final: 'Complete',
}

export function StateProgress() {
  const { currentState, stateHistory, status } = useStreamStore()
  const completedStates = new Set(stateHistory.map((h) => h.state))

  // Show only the states for the active path (plan-execute vs react)
  const isPlanExecute = completedStates.has('plan') || currentState === 'plan' || currentState === 'execute'
  const states = isPlanExecute ? PLAN_EXECUTE_STATES : REACT_STATES

  if (status === 'idle') return null

  return (
    <div className="flex items-center gap-1 px-4 py-2 bg-slate-100 border-b border-slate-200 overflow-x-auto">
      {states.map((state) => {
        const isActive = currentState === state
        const isCompleted = completedStates.has(state)
        const timing = stateHistory.find((h) => h.state === state)

        return (
          <div key={state} className="flex items-center gap-1">
            <div
              className={cn(
                'px-2.5 py-1 rounded text-xs font-medium transition-colors',
                isActive && 'bg-blue-600 text-white animate-pulse',
                isCompleted && !isActive && 'bg-emerald-100 text-emerald-800',
                !isActive && !isCompleted && 'bg-slate-200 text-slate-500',
              )}
            >
              {STATE_LABELS[state] || state}
              {timing && (
                <span className="ml-1 opacity-70">
                  {timing.duration_ms < 1000
                    ? `${Math.round(timing.duration_ms)}ms`
                    : `${(timing.duration_ms / 1000).toFixed(1)}s`}
                </span>
              )}
            </div>
            {state !== 'emit_final' && (
              <span className="text-slate-300 text-xs">&rarr;</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

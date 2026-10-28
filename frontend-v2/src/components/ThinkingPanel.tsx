import { useStreamStore } from '../stores/streamStore'

export function ThinkingPanel() {
  const { thinkingMessages } = useStreamStore()

  if (thinkingMessages.length === 0) return null

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Agent Reasoning</h3>
      <div className="space-y-1.5">
        {thinkingMessages.map((msg, i) => (
          <div key={i} className="text-xs text-slate-600 bg-slate-50 rounded px-2.5 py-1.5 border border-slate-200">
            <p>{msg.content}</p>
            {msg.structured?.intent && (
              <div className="mt-1 flex gap-2 flex-wrap">
                <span className="inline-block px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded text-[10px] font-medium">
                  {msg.structured.intent}
                </span>
                {msg.structured.complexity && (
                  <span className="inline-block px-1.5 py-0.5 bg-amber-100 text-amber-700 rounded text-[10px] font-medium">
                    {msg.structured.complexity}
                  </span>
                )}
                {msg.structured.strategy && (
                  <span className="inline-block px-1.5 py-0.5 bg-purple-100 text-purple-700 rounded text-[10px] font-medium">
                    {msg.structured.strategy}
                  </span>
                )}
              </div>
            )}
            {msg.structured?.plan_steps && msg.structured.plan_steps.length > 0 && (
              <div className="mt-1.5 space-y-0.5">
                {msg.structured.plan_steps.map((step) => (
                  <div key={step.step} className="text-[10px] text-slate-500">
                    {step.step + 1}. <span className="font-medium text-slate-700">{step.tool}</span> — {step.description}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

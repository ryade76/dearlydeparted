import { useEffect, useState } from 'react'
import { fetchHealth } from '../api/client'
import { cn } from '../lib/cn'

interface HealthData {
  status: string
  checks: Record<string, { status: string; error?: string }>
}

export function HealthStatus() {
  const [health, setHealth] = useState<HealthData | null>(null)

  useEffect(() => {
    const check = () => {
      fetchHealth()
        .then((data) => setHealth(data as unknown as HealthData))
        .catch(() => setHealth(null))
    }
    check()
    const interval = setInterval(check, 30000)
    return () => clearInterval(interval)
  }, [])

  if (!health) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-slate-400">
        <span className="w-2 h-2 rounded-full bg-slate-300" />
        Connecting...
      </div>
    )
  }

  return (
    <div className="flex items-center gap-3 text-xs">
      <div className="flex items-center gap-1.5">
        <span
          className={cn(
            'w-2 h-2 rounded-full',
            health.status === 'ok' ? 'bg-emerald-500' : 'bg-amber-500',
          )}
        />
        <span className="text-slate-500 font-medium">
          {health.status === 'ok' ? 'All Systems OK' : 'Degraded'}
        </span>
      </div>
      {Object.entries(health.checks).map(([name, check]) => (
        <div key={name} className="flex items-center gap-1" title={check.error || ''}>
          <span
            className={cn(
              'w-1.5 h-1.5 rounded-full',
              check.status === 'ok' ? 'bg-emerald-400' : 'bg-red-400',
            )}
          />
          <span className="text-slate-400">{name}</span>
        </div>
      ))}
    </div>
  )
}

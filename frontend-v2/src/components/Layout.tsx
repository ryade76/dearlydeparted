import { type ReactNode, useState } from 'react'
import { HealthStatus } from './HealthStatus'
import { StateProgress } from './StateProgress'
import { ThinkingPanel } from './ThinkingPanel'
import { ToolCallTimeline } from './ToolCallTimeline'
import { ContextBudget } from './ContextBudget'
import { TraceViewer } from './TraceViewer'
import { SessionSidebar } from './SessionSidebar'
import { useStreamStore } from '../stores/streamStore'
import { useSessionStore } from '../stores/sessionStore'

type Tab = 'tools' | 'trace' | 'context'

interface LayoutProps {
  children: ReactNode
}

export function Layout({ children }: LayoutProps) {
  const [activeTab, setActiveTab] = useState<Tab>('tools')
  const [showSidebar, setShowSidebar] = useState(true)
  const { status } = useStreamStore()
  const activeSession = useSessionStore((s) => s.sessions.find((ses) => ses.id === s.activeSessionId))
  const showPanel = status !== 'idle'

  return (
    <div className="h-screen flex flex-col bg-slate-50">
      {/* Header */}
      <header className="bg-slate-900 text-white px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowSidebar(!showSidebar)}
            className="text-slate-400 hover:text-white transition-colors text-sm"
            title={showSidebar ? 'Hide sessions' : 'Show sessions'}
          >
            {showSidebar ? '\u2630' : '\u2630'}
          </button>
          <h1 className="text-lg font-bold tracking-tight">Dearly Departed</h1>
          <span className="text-xs text-slate-400 font-medium">Knowledge Capture Assistant</span>
          <span className="text-[10px] bg-blue-600 text-white px-1.5 py-0.5 rounded font-medium">v2</span>
          {activeSession && (
            <span className="text-xs text-slate-500 truncate max-w-[200px]">
              — {activeSession.title}
            </span>
          )}
        </div>
        <HealthStatus />
      </header>

      {/* State progress bar */}
      <StateProgress />

      {/* Main content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Session sidebar */}
        {showSidebar && (
          <div className="w-56 flex-shrink-0">
            <SessionSidebar />
          </div>
        )}

        {/* Center panel: Chat */}
        <div className={`flex-1 min-w-0 ${showPanel ? 'border-r border-slate-200' : ''}`}>
          {children}
        </div>

        {/* Right panel: Activity */}
        {showPanel && (
          <div className="w-2/5 flex flex-col bg-white">
            {/* Tab bar */}
            <div className="flex border-b border-slate-200">
              {(['tools', 'trace', 'context'] as Tab[]).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  className={`flex-1 px-3 py-2 text-xs font-medium transition-colors ${
                    activeTab === tab
                      ? 'text-blue-600 border-b-2 border-blue-600 bg-blue-50'
                      : 'text-slate-500 hover:text-slate-700'
                  }`}
                >
                  {tab === 'tools' ? 'Tools' : tab === 'trace' ? 'Trace' : 'Context'}
                </button>
              ))}
            </div>

            {/* Tab content */}
            <div className="flex-1 overflow-y-auto p-3 space-y-4">
              {activeTab === 'tools' && (
                <>
                  <ThinkingPanel />
                  <ToolCallTimeline />
                </>
              )}
              {activeTab === 'context' && <ContextBudget />}
              {activeTab === 'trace' && <TraceViewer />}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

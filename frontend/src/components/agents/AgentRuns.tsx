import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAgents, type AgentRun, type RunStatus } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { StatusBadge } from './StatusBadge'

const FILTERS: (RunStatus | 'all')[] = ['all', 'running', 'done', 'failed', 'budget_exceeded', 'cancelled']

export default function AgentRuns() {
  const { listRuns } = useAgents()
  const [status, setStatus] = useState<RunStatus | 'all'>('all')
  const [runs, setRuns] = useState<AgentRun[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setRuns(null)
    setError(null)
    listRuns({ status: status === 'all' ? undefined : status, limit: 100 }).then(setRuns).catch(e => setError(e.message))
  }, [listRuns, status])

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Agent runs</h1>
        <div className="flex gap-2">
          <Link to="/ui/agents" className="px-3 py-1.5 border rounded text-sm">Agents</Link>
          <Link to="/ui/agents/runs/new" className="px-3 py-1.5 bg-primary text-white rounded text-sm">New ad-hoc run</Link>
        </div>
      </div>
      <div className="flex gap-1">
        {FILTERS.map(f => (
          <button key={f} className={`px-2 py-1 rounded text-xs ${f === status ? 'bg-primary text-white' : 'bg-slate-100'}`} onClick={() => setStatus(f)}>{f}</button>
        ))}
      </div>
      {error && <div className="p-3 bg-red-50 text-red-700 rounded text-sm">{error}</div>}
      {runs === null && !error && <div className="text-slate-500">Loading runs...</div>}
      {runs?.length === 0 && <div className="text-slate-500">No runs</div>}
      <ul className="divide-y bg-white rounded-xl shadow-sm">
        {runs?.map(r => (
          <li key={r.id} className="p-3 flex items-center justify-between text-sm">
            <Link to={`/ui/agents/runs/${r.id}`} className="text-primary truncate max-w-lg">{r.starred ? '★ ' : ''}{r.input}</Link>
            <span className="flex items-center gap-3">
              <span className="font-mono text-xs text-slate-400">{r.model}</span>
              <StatusBadge status={r.status} />
              <span className="text-xs text-slate-400">{formatRelativeTime(r.created_at)} · ${r.cost_usd}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

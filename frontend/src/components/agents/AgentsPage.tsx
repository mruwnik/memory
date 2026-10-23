import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAgents, type AgentDefinition } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { StatusBadge } from './StatusBadge'

export default function AgentsPage() {
  const { listDefinitions } = useAgents()
  const [definitions, setDefinitions] = useState<AgentDefinition[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listDefinitions().then(setDefinitions).catch(e => setError(e.message))
  }, [listDefinitions])

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Agents</h1>
        <div className="flex gap-2">
          <Link to="/ui/agents/runs" className="px-3 py-1.5 border rounded text-sm">Runs</Link>
          <Link to="/ui/agents/new" className="px-3 py-1.5 bg-primary text-white rounded text-sm">New agent</Link>
        </div>
      </div>
      {error && <div className="p-3 bg-red-50 text-red-700 rounded text-sm">{error}</div>}
      {definitions === null && !error && <div className="text-slate-500">Loading agents...</div>}
      {definitions?.length === 0 && <div className="text-slate-500">No agents yet</div>}
      <ul className="divide-y bg-white rounded-xl shadow-sm">
        {definitions?.map(d => (
          <li key={d.id} className="p-4 flex items-center justify-between">
            <div>
              <Link to={`/ui/agents/${d.id}`} className="font-medium text-primary">{d.name}</Link>
              <div className="text-sm text-slate-500">{d.description}</div>
              <div className="text-xs text-slate-400 font-mono">{d.spec.model}</div>
            </div>
            {d.last_run && (
              <div className="text-right text-sm">
                <StatusBadge status={d.last_run.status} />
                <div className="text-xs text-slate-400">{formatRelativeTime(d.last_run.created_at)} · ${d.last_run.cost_usd}</div>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAgents, type ExternalServer } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { ServerForm } from './ServerForm'
import { ServerStatusBadge } from './ServerStatusBadge'

const AUTH_LABELS: Record<ExternalServer['auth_type'], string> = { oauth: 'OAuth', headers: 'Headers', none: 'No auth' }

const hostOf = (url: string) => {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

export default function ServersPage() {
  const { listServers, upsertServer } = useAgents()
  const [servers, setServers] = useState<ExternalServer[] | null>(null)
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    listServers().then(setServers).catch(e => setError(e.message))
  }, [listServers])
  useEffect(load, [load])

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-4">
      <Link to="/ui/agents" className="text-sm text-primary">← Agents</Link>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">MCP servers</h1>
        <button className="px-3 py-1.5 bg-primary text-white rounded text-sm" onClick={() => setAdding(a => !a)}>
          {adding ? 'Close' : 'Add server'}
        </button>
      </div>
      <p className="text-sm text-slate-500">
        Remote MCP servers your agents can call. Only you can use the servers you add.
      </p>
      {adding && (
        <div className="bg-white rounded-xl shadow-sm p-4">
          <ServerForm onSubmit={async values => {
            const result = await upsertServer(values)
            load()
            return result
          }} />
        </div>
      )}
      {error && <div className="p-3 bg-red-50 text-red-700 rounded text-sm">{error}</div>}
      {servers === null && !error && <div className="text-slate-500">Loading servers...</div>}
      {servers?.length === 0 && <div className="text-slate-500">No MCP servers yet</div>}
      {!!servers?.length && (
        <table className="w-full bg-white rounded-xl shadow-sm text-sm">
          <thead className="text-left text-xs text-slate-500">
            <tr>
              <th className="p-3">Name</th>
              <th className="p-3">Host</th>
              <th className="p-3">Auth</th>
              <th className="p-3">Status</th>
              <th className="p-3">Tools</th>
              <th className="p-3">Refreshed</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {servers.map(s => (
              <tr key={s.id}>
                <td className="p-3"><Link to={`/ui/agents/servers/${s.id}`} className="text-primary font-medium">{s.name}</Link></td>
                <td className="p-3 font-mono text-xs">{hostOf(s.url)}</td>
                <td className="p-3">{AUTH_LABELS[s.auth_type]}</td>
                <td className="p-3"><ServerStatusBadge status={s.status} /></td>
                <td className="p-3">{s.tools.length}</td>
                <td className="p-3 text-xs text-slate-400">{s.tools_refreshed_at ? formatRelativeTime(s.tools_refreshed_at) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

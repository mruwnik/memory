import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useAgents, type ExternalServer, type ToolDiff } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { ServerForm } from './ServerForm'
import { ServerStatusBadge } from './ServerStatusBadge'

const buttonClass = 'px-3 py-1.5 border rounded text-sm disabled:opacity-50'

const diffSummary = (diff: ToolDiff) =>
  [
    diff.added.length && `added ${diff.added.join(', ')}`,
    diff.removed.length && `removed ${diff.removed.join(', ')}`,
    diff.changed.length && `changed ${diff.changed.join(', ')}`,
  ].filter(Boolean).join('; ') || 'no changes'

export default function ServerDetail() {
  const { id } = useParams()
  const serverId = Number(id)
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { listServers, upsertServer, authorizeServer, refreshServerTools, disconnectServer, deleteServer } = useAgents()
  const [server, setServer] = useState<ExternalServer | null | undefined>(undefined)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [diff, setDiff] = useState<ToolDiff | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    listServers()
      .then(all => setServer(all.find(s => s.id === serverId) ?? null))
      .catch(e => setError(e.message))
  }, [listServers, serverId])
  useEffect(load, [load])

  const act = async (work: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    setMessage(null)
    try {
      await work()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!Number.isInteger(serverId) || server === null) return <div className="p-6 text-slate-500">Unknown MCP server</div>
  if (server === undefined) return <div className="p-6 text-slate-500">{error ?? 'Loading server...'}</div>

  const reauthorize = () => act(async () => {
    window.open(await authorizeServer(server.id), '_blank', 'noopener,noreferrer')
    setMessage('Finish authorizing in the tab that just opened, then reload this page.')
  })
  const refresh = () => act(async () => {
    const result = await refreshServerTools(server.id)
    setServer(result.server)
    setDiff(result.diff)
  })
  const disconnect = () => act(async () => {
    setServer(await disconnectServer(server.id))
  })
  const remove = () => {
    if (!window.confirm(`Delete MCP server "${server.name}"?`)) return
    act(async () => {
      await deleteServer(server.id)
      navigate('/ui/agents/servers')
    })
  }

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-4">
      <Link to="/ui/agents/servers" className="text-sm text-primary">← MCP servers</Link>
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-semibold">{server.name}</h1>
        <ServerStatusBadge status={server.status} />
      </div>
      <div className="text-sm text-slate-500 font-mono">{server.url} · {server.transport}</div>
      {params.get('connected') && server.status === 'connected' && (
        <div className="p-3 bg-green-50 text-green-800 rounded text-sm">Connected.</div>
      )}
      {server.status_detail && server.status !== 'connected' && (
        <div className="p-3 bg-amber-50 text-amber-800 rounded text-sm">{server.status_detail}</div>
      )}
      {error && <div className="p-3 bg-red-50 text-red-700 rounded text-sm">{error}</div>}
      {message && <div className="p-3 bg-blue-50 text-blue-800 rounded text-sm">{message}</div>}
      {diff && <div className="p-3 bg-slate-50 rounded text-sm">Tools refreshed: {diffSummary(diff)}
        {diff.dropped.map(d => <div key={d.name} className="text-amber-700">{d.name} unavailable: {d.reason}</div>)}
      </div>}

      <div className="flex flex-wrap gap-2">
        <button className={buttonClass} disabled={busy} onClick={refresh}>Refresh tools</button>
        {server.auth_type === 'oauth' && <button className={buttonClass} disabled={busy} onClick={reauthorize}>Re-authorize</button>}
        {server.has_token && <button className={buttonClass} disabled={busy} onClick={disconnect}>Disconnect</button>}
        <button className={buttonClass} disabled={busy} onClick={() => setEditing(e => !e)}>{editing ? 'Cancel edit' : 'Edit'}</button>
        <button className={`${buttonClass} border-red-300 text-red-700`} disabled={busy} onClick={remove}>Delete</button>
      </div>

      {editing && (
        <div className="bg-white rounded-xl shadow-sm p-4">
          <ServerForm server={server} onSubmit={async values => {
            const result = await upsertServer({ ...values, serverId: server.id })
            setServer(result.server)
            return result
          }} />
        </div>
      )}

      <section className="space-y-2">
        <h2 className="font-semibold">Tools</h2>
        {server.tools.length === 0 && <div className="text-sm text-slate-500">No tools cached yet</div>}
        <ul className="divide-y bg-white rounded-xl shadow-sm">
          {server.tools.map(tool => (
            <li key={tool.name} className="p-3 text-sm space-y-1">
              <div className="flex items-center gap-2">
                <span className="font-mono">{tool.name}</span>
                {tool.read_only
                  ? <span className="text-xs text-green-700">read-only</span>
                  : <span className="text-xs text-amber-600">side effects</span>}
                {tool.changed_at && (
                  <span className="text-xs text-slate-400">as of {formatRelativeTime(tool.changed_at)}</span>
                )}
              </div>
              <div className="text-slate-500">{tool.description}</div>
              <details>
                <summary className="text-xs text-slate-400 cursor-pointer">Input schema</summary>
                <pre className="text-xs bg-slate-50 p-2 rounded overflow-x-auto">{JSON.stringify(tool.input_schema, null, 2)}</pre>
              </details>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}

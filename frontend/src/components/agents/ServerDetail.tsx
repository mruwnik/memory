import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useAgents, type ExternalServer, type ToolDiff } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { ServerForm } from './ServerForm'
import { ServerStatusBadge } from './ServerStatusBadge'
import { Badge, Button, Card, EmptyState, List, ListItem, Notice, Page, Section } from '@/components/shared/ui'

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

  const back = { to: '/ui/agents/servers', label: 'MCP servers' }
  if (!Number.isInteger(serverId) || server === null) return <Page back={back} title="Unknown MCP server" />
  if (server === undefined) return <Page back={back} title="MCP server" loading="Loading server..." error={error} />

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
    <Page
      back={back}
      title={<>{server.name} <ServerStatusBadge status={server.status} /></>}
      description={<span className="font-mono break-all">{server.url} · {server.transport}</span>}
      actions={<>
        <Button disabled={busy} onClick={refresh}>Refresh tools</Button>
        {server.auth_type === 'oauth' && <Button disabled={busy} onClick={reauthorize}>Re-authorize</Button>}
        {server.has_token && <Button disabled={busy} onClick={disconnect}>Disconnect</Button>}
        <Button disabled={busy} aria-expanded={editing} onClick={() => setEditing(e => !e)}>{editing ? 'Cancel edit' : 'Edit'}</Button>
        <Button variant="danger" disabled={busy} onClick={remove}>Delete</Button>
      </>}
    >
    {params.get('connected') && server.status === 'connected' && <Notice tone="success">Connected.</Notice>}
    {server.status_detail && server.status !== 'connected' && <Notice tone="warning">{server.status_detail}</Notice>}
    {error && <Notice tone="error">{error}</Notice>}
    {message && <Notice tone="info">{message}</Notice>}
    {diff && (
      <Notice tone="info">
        Tools refreshed: {diffSummary(diff)}
        {diff.dropped.map(d => <div key={d.name} className="text-amber-800">{d.name} unavailable: {d.reason}</div>)}
      </Notice>
    )}

    {editing && (
      <Card>
        <ServerForm server={server} onSubmit={async values => {
          const result = await upsertServer({ ...values, serverId: server.id })
          setServer(result.server)
          return result
        }} />
      </Card>
    )}

    <Section title="Tools">
      {server.tools.length === 0 && <EmptyState message="No tools cached yet" />}
      {!!server.tools.length && (
        <List>
          {server.tools.map(tool => (
            <ListItem key={tool.name} className="block space-y-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono">{tool.name}</span>
                {tool.read_only ? <Badge tone="success">read-only</Badge> : <Badge>side effects</Badge>}
                {tool.changed_at && <span className="text-xs text-slate-600">as of {formatRelativeTime(tool.changed_at)}</span>}
              </div>
              <div className="text-slate-600">{tool.description}</div>
              <details>
                <summary className="text-xs text-slate-600 cursor-pointer">Input schema</summary>
                <pre className="text-xs bg-slate-50 p-2 rounded overflow-x-auto">{JSON.stringify(tool.input_schema, null, 2)}</pre>
              </details>
            </ListItem>
          ))}
        </List>
      )}
    </Section>
  </Page>
)
}

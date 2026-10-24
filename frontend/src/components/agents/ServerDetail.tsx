import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useAgents, type ExternalServer, type ToolDiff } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { ServerForm } from './ServerForm'
import { ServerStatusBadge } from './ServerStatusBadge'
import { Badge, Button, ConfirmButton, DetailList, Disclosure, EmptyState, List, Modal, Notice, Page, Row, Section } from '@/components/shared/ui'

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
  const remove = () => act(async () => {
    await deleteServer(server.id)
    navigate('/ui/agents/servers')
  })

  return (
    <Page
      back={back}
      title={<>{server.name} <ServerStatusBadge status={server.status} /></>}
      actions={<>
        <Button disabled={busy} onClick={refresh}>Refresh tools</Button>
        {server.auth_type === 'oauth' && <Button disabled={busy} onClick={reauthorize}>Re-authorize</Button>}
        {server.has_token && <Button disabled={busy} onClick={disconnect}>Disconnect</Button>}
        <Button disabled={busy} onClick={() => setEditing(true)}>Edit</Button>
        <ConfirmButton size="md" disabled={busy} confirm={`Delete MCP server "${server.name}"?`} onConfirm={remove} />
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

    <DetailList items={[
      ['URL', <span className="font-mono break-all">{server.url}</span>],
      ['Transport', server.transport],
      ['Tools refreshed', server.tools_refreshed_at && formatRelativeTime(server.tools_refreshed_at)],
    ]} />

    {editing && (
      <Modal title={`Edit ${server.name}`} onClose={() => setEditing(false)}>
        <ServerForm server={server} onSubmit={async values => {
          const result = await upsertServer({ ...values, serverId: server.id })
          setServer(result.server)
          return result
        }} />
      </Modal>
    )}

    <Section title="Tools">
      {server.tools.length === 0 && <EmptyState message="No tools cached yet" />}
      {!!server.tools.length && (
        <List>
          {server.tools.map(tool => (
            <Row
              key={tool.name}
              title={<span className="font-mono">{tool.name}</span>}
              badges={tool.read_only ? <Badge tone="success">read-only</Badge> : <Badge>side effects</Badge>}
              meta={[tool.changed_at && `as of ${formatRelativeTime(tool.changed_at)}`]}
            >
              {tool.description && <p className="text-slate-600">{tool.description}</p>}
              <Disclosure summary="Input schema" className="border-0 bg-transparent">
                <pre className="text-xs bg-slate-50 p-2 rounded overflow-x-auto">{JSON.stringify(tool.input_schema, null, 2)}</pre>
              </Disclosure>
            </Row>
          ))}
        </List>
      )}
    </Section>
  </Page>
)
}

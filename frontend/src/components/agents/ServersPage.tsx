import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAgents, type ExternalServer } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { AUTH_LABELS, ServerForm } from './ServerForm'
import { ServerStatusBadge } from './ServerStatusBadge'
import { Button, EmptyState, List, Modal, Page, Row } from '@/components/shared/ui'

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
  const navigate = useNavigate()

  const load = useCallback(() => {
    setError(null)
    listServers().then(setServers).catch(e => setError(e.message))
  }, [listServers])
  useEffect(load, [load])

  return (
    <Page
      back={{ to: '/ui/agents', label: 'Agents' }}
      title="MCP servers"
      description="Remote MCP servers your agents can call. Only you can use the servers you add."
      actions={<Button variant="primary" onClick={() => setAdding(true)}>Add server</Button>}
      loading={servers === null && 'Loading servers...'}
      error={error}
      onRetry={load}
    >
      {adding && (
        <Modal title="Add MCP server" onClose={() => setAdding(false)}>
          <ServerForm onSubmit={async values => {
            const result = await upsertServer(values)
            // OAuth and failed setups keep the modal open to say what to do next.
            if (!result.authorize_url && !result.error) navigate(`/ui/agents/servers/${result.server.id}`)
            else load()
            return result
          }} />
        </Modal>
      )}
      {servers?.length === 0 && <EmptyState message="No MCP servers yet" actionLabel="Add server" onAction={() => setAdding(true)} />}
      {!!servers?.length && (
        <List>
          {servers.map(s => (
            <Row
              key={s.id}
              to={`/ui/agents/servers/${s.id}`}
              title={s.name}
              badges={<ServerStatusBadge status={s.status} />}
              meta={[
                <span className="font-mono">{hostOf(s.url)}</span>,
                AUTH_LABELS[s.auth_type],
                `${s.tools.length} tool${s.tools.length === 1 ? '' : 's'}`,
                s.tools_refreshed_at && `Refreshed ${formatRelativeTime(s.tools_refreshed_at)}`,
              ]}
            />
          ))}
        </List>
      )}
    </Page>
  )
}

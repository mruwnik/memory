import { useCallback, useEffect, useState } from 'react'
import { useAgents, type ExternalServer } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { ServerForm } from './ServerForm'
import { ServerStatusBadge } from './ServerStatusBadge'
import { Button, EmptyState, List, Modal, Page, Row } from '@/components/shared/ui'

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
    <Page
      back={{ to: '/ui/agents', label: 'Agents' }}
      title="MCP servers"
      description="Remote MCP servers your agents can call. Only you can use the servers you add."
      actions={<Button variant="primary" onClick={() => setAdding(true)}>Add server</Button>}
      loading={servers === null && 'Loading servers...'}
      error={error}
    >
      {adding && (
        <Modal title="Add MCP server" onClose={() => setAdding(false)}>
          <ServerForm onSubmit={async values => {
            const result = await upsertServer(values)
            load()
            return result
          }} />
        </Modal>
      )}
      {servers?.length === 0 && <EmptyState message="No MCP servers yet" />}
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

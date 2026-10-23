import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAgents, type ExternalServer } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { ServerForm } from './ServerForm'
import { ServerStatusBadge } from './ServerStatusBadge'
import { Button, Card, EmptyState, Page, Table } from '@/components/shared/ui'

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
      actions={
        <Button variant={adding ? 'secondary' : 'primary'} aria-expanded={adding} onClick={() => setAdding(a => !a)}>
          {adding ? 'Close' : 'Add server'}
        </Button>
      }
      loading={servers === null && 'Loading servers...'}
      error={error}
    >
      {adding && (
        <Card>
          <ServerForm onSubmit={async values => {
            const result = await upsertServer(values)
            load()
            return result
          }} />
        </Card>
      )}
      {servers?.length === 0 && <EmptyState message="No MCP servers yet" />}
      {!!servers?.length && (
        <Table>
          <thead>
            <tr><th>Name</th><th>Host</th><th>Auth</th><th>Status</th><th>Tools</th><th>Refreshed</th></tr>
          </thead>
          <tbody>
            {servers.map(s => (
              <tr key={s.id}>
                <td><Link to={`/ui/agents/servers/${s.id}`} className="text-primary font-medium">{s.name}</Link></td>
                <td className="font-mono text-xs">{hostOf(s.url)}</td>
                <td>{AUTH_LABELS[s.auth_type]}</td>
                <td><ServerStatusBadge status={s.status} /></td>
                <td>{s.tools.length}</td>
                <td className="text-xs text-slate-600">{s.tools_refreshed_at ? formatRelativeTime(s.tools_refreshed_at) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Page>
  )
}

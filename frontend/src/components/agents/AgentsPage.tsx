import { useEffect, useState } from 'react'
import { useAgents, type AgentDefinition } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { StatusBadge } from './StatusBadge'
import { Badge, ButtonLink, EmptyState, List, Page, Row } from '@/components/shared/ui'

const plural = (n: number, word: string) => `${n} ${word}${n > 1 ? 's' : ''}`

export default function AgentsPage() {
  const { listDefinitions } = useAgents()
  const [definitions, setDefinitions] = useState<AgentDefinition[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listDefinitions().then(setDefinitions).catch(e => setError(e.message))
  }, [listDefinitions])

  return (
    <Page
      title="Agents"
      description="Agents run a model with your memory tools on demand, on a schedule, or when new items arrive."
      actions={<>
        <ButtonLink to="/ui/agents/new" variant="primary">New agent</ButtonLink>
        <ButtonLink to="/ui/agents/runs">Runs</ButtonLink>
        <ButtonLink to="/ui/agents/servers">MCP servers</ButtonLink>
        <ButtonLink to="/ui/agents/keys">API keys</ButtonLink>
      </>}
      loading={definitions === null && 'Loading agents...'}
      error={error}
    >
      {definitions?.length === 0 && <EmptyState message="No agents yet" />}
      {!!definitions?.length && (
        <List>
          {definitions.map(d => (
            <Row
              key={d.id}
              to={`/ui/agents/${d.id}`}
              title={d.name}
              badges={<>
                {d.last_run && <StatusBadge status={d.last_run.status} />}
                {!!d.changed_tools?.length && (
                  <Badge tone="warning" title={d.changed_tools.join(', ')}>{plural(d.changed_tools.length, 'external tool')} changed</Badge>
                )}
                {!!d.triggers?.tripped && <Badge tone="warning">{plural(d.triggers.tripped, 'trigger')} tripped</Badge>}
              </>}
              meta={[
                <span className="font-mono">{d.spec.model}</span>,
                d.last_run && `Last run ${formatRelativeTime(d.last_run.created_at)}`,
                d.last_run && `$${d.last_run.cost_usd}`,
              ]}
            >
              {d.description && <p className="text-slate-600">{d.description}</p>}
            </Row>
          ))}
        </List>
      )}
    </Page>
  )
}

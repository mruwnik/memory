import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAgents, type AgentDefinition } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { StatusBadge } from './StatusBadge'
import { Badge, ButtonLink, EmptyState, List, ListItem, Page } from '@/components/shared/ui'

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
            <ListItem key={d.id}>
              <div className="min-w-0 space-y-0.5">
                <Link to={`/ui/agents/${d.id}`} className="font-medium text-primary">{d.name}</Link>
                <div className="text-slate-600">{d.description}</div>
                <div className="text-xs text-slate-600 font-mono">{d.spec.model}</div>
                <div className="flex flex-wrap gap-1">
                  {!!d.changed_tools?.length && (
                    <Badge tone="warning" title={d.changed_tools.join(', ')}>{plural(d.changed_tools.length, 'external tool')} changed</Badge>
                  )}
                  {!!d.triggers?.tripped && <Badge tone="warning">{plural(d.triggers.tripped, 'trigger')} tripped</Badge>}
                </div>
              </div>
              {d.last_run && (
                <div className="text-right shrink-0">
                  <StatusBadge status={d.last_run.status} />
                  <div className="text-xs text-slate-600">{formatRelativeTime(d.last_run.created_at)} · ${d.last_run.cost_usd}</div>
                </div>
              )}
            </ListItem>
          ))}
        </List>
      )}
    </Page>
  )
}

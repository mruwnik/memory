import { useEffect, useState } from 'react'
import { useAgents, type AgentDefinition, type SystemAgentEntry } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { StatusBadge } from './StatusBadge'
import { Badge, ButtonLink, EmptyState, List, Notice, Page, Row, Section } from '@/components/shared/ui'
import { formatCost } from '@/utils/format'

const plural = (n: number, word: string) => `${n} ${word}${n > 1 ? 's' : ''}`

export default function AgentsPage() {
  const { listDefinitions, listSystemAgents } = useAgents()
  const [definitions, setDefinitions] = useState<AgentDefinition[] | null>(null)
  const [systemAgents, setSystemAgents] = useState<SystemAgentEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [systemError, setSystemError] = useState<string | null>(null)

  useEffect(() => {
    listDefinitions().then(setDefinitions).catch(e => setError(e.message))
    listSystemAgents().then(setSystemAgents).catch(e => setSystemError(e.message))
  }, [listDefinitions, listSystemAgents])

  return (
    <Page
      title="Agents"
      description="Agents run a model with your memory tools on demand, on a schedule, or when new items arrive."
      actions={<>
        <ButtonLink to="/ui/agents/new" variant="primary">New agent</ButtonLink>
        <ButtonLink to="/ui/agents/runs">Runs</ButtonLink>
        <ButtonLink to="/ui/agents/servers">MCP servers</ButtonLink>
        <ButtonLink to="/ui/keys">API keys</ButtonLink>
      </>}
      loading={definitions === null && 'Loading agents...'}
      error={error}
    >
      {definitions?.length === 0 && <EmptyState message="You have no agents yet" />}
      {!!definitions?.length && (
        <List>
          {definitions.map(d => (
            <Row
              key={d.id}
              to={`/ui/agents/${d.id}`}
              title={d.name}
              badges={<>
                {!!d.problems?.length && <Badge tone="danger" title={d.problems.join('\n')}>!</Badge>}
                {d.last_run && <StatusBadge status={d.last_run.status} />}
                {!!d.changed_tools?.length && (
                  <Badge tone="warning" title={d.changed_tools.join(', ')}>{plural(d.changed_tools.length, 'external tool')} changed</Badge>
                )}
                {!!d.triggers?.tripped && <Badge tone="warning">{plural(d.triggers.tripped, 'trigger')} tripped</Badge>}
              </>}
              meta={[
                <span className="font-mono">{d.spec.model}</span>,
                d.last_run && `Last run ${formatRelativeTime(d.last_run.created_at)}`,
                d.last_run && formatCost(d.last_run.cost_usd),
              ]}
            >
              {d.description && <p className="text-slate-600">{d.description}</p>}
            </Row>
          ))}
        </List>
      )}
      {(systemError || !!systemAgents?.length) && (
        <Section title="System agents" description="Defined by memory and run with your default key for each provider. Read-only.">
          {systemError && <Notice tone="error">{systemError}</Notice>}
          {!!systemAgents?.length && (
            <List>
              {systemAgents.map(a => (
                <Row
                  key={a.slug}
                  title={a.title}
                  badges={<>
                    <Badge tone="neutral">System</Badge>
                    {a.problem && <Badge tone="danger" title={a.problem}>!</Badge>}
                  </>}
                  meta={[<span className="font-mono">{a.model}</span>, a.use]}
                >
                  <p className="text-slate-600">{a.description}</p>
                </Row>
              ))}
            </List>
          )}
        </Section>
      )}
    </Page>
  )
}

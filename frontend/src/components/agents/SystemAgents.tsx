import type { SystemAgentEntry } from '@/hooks/useAgents'
import { Badge, List, Notice, Row, RowError, Section } from '@/components/shared/ui'

interface Props {
  agents: SystemAgentEntry[] | null
  error: string | null
  onRetry: () => void
  description: string
}

export const SystemAgents = ({ agents, error, onRetry, description }: Props) => {
  if (!error && !agents?.length) return null
  return (
    <Section title="System agents" description={description}>
      {error && <Notice tone="error" onRetry={onRetry}>{error}</Notice>}
      {!!agents?.length && (
        <List>
          {agents.map(a => (
            <Row
              key={a.slug}
              title={a.title}
              badges={<>
                <Badge tone="neutral">System</Badge>
                {a.problem && <Badge tone="danger">Can't run</Badge>}
              </>}
              meta={[<span className="font-mono">{a.model}</span>, a.use]}
            >
              <p className="text-slate-600">{a.description}</p>
              {a.problem && <RowError>{a.problem}</RowError>}
            </Row>
          ))}
        </List>
      )}
    </Section>
  )
}

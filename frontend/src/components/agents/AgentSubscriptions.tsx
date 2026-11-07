import { useCallback, useEffect, useState } from 'react'
import { useAgents, type Subscription } from '@/hooks/useAgents'
import { Badge, ButtonLink, EmptyState, List, LoadingState, Notice, Row, Section } from '@/components/shared/ui'
import { origin, sourceKind, SOURCE_LABELS, subscriptionState } from './subscriptions'

/** What starts this agent's runs; they're managed on the Subscriptions page. */
export function AgentSubscriptions({ definitionId }: { definitionId: number }) {
  const { listSubscriptions } = useAgents()
  const [subscriptions, setSubscriptions] = useState<Subscription[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setError(null)
    return listSubscriptions({ definitionId }).then(r => setSubscriptions(r.subscriptions)).catch(e => setError(e.message))
  }, [listSubscriptions, definitionId])
  useEffect(() => { load() }, [load])

  return (
    <Section
      title="Subscriptions"
      description="Schedules and item filters that run this agent. Each run uses the saved version."
      actions={<>
        <ButtonLink size="sm" to={`/agents/subscriptions?agent=${definitionId}&new=1`}>New subscription</ButtonLink>
        <ButtonLink size="sm" to={`/agents/subscriptions?agent=${definitionId}`}>Manage</ButtonLink>
      </>}
    >
      {error && <Notice tone="error" onRetry={load}>{error}</Notice>}
      {subscriptions === null && !error && <LoadingState message="Loading subscriptions..." />}
      {subscriptions?.length === 0 && <EmptyState message="Nothing runs this agent automatically yet." />}
      {!!subscriptions?.length && (
        <List>
          {subscriptions.map(s => {
            const state = subscriptionState(s)
            return (
              <Row key={s.id} title={s.name} muted={!s.enabled}
                badges={<><Badge>{SOURCE_LABELS[sourceKind(s)]}</Badge>{state && <Badge tone={state.tone}>{state.label}</Badge>}</>}
                meta={[origin(s), s.activity.pending > 0 && `${s.activity.pending} waiting`]} />
            )
          })}
        </List>
      )}
    </Section>
  )
}

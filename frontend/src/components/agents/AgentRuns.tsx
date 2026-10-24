import { useEffect, useState } from 'react'
import { useAgents, type AgentRun, type RunStatus } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { StatusBadge } from './StatusBadge'
import { ButtonLink, EmptyState, List, LoadingState, Notice, Page, Row, ToggleGroup, Toolbar } from '@/components/shared/ui'

const FILTERS: (RunStatus | 'all')[] = ['all', 'running', 'done', 'failed', 'budget_exceeded', 'cancelled']

export default function AgentRuns() {
  const { listRuns } = useAgents()
  const [status, setStatus] = useState<RunStatus | 'all'>('all')
  const [runs, setRuns] = useState<AgentRun[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setRuns(null)
    setError(null)
    listRuns({ status: status === 'all' ? undefined : status, limit: 100 }).then(setRuns).catch(e => setError(e.message))
  }, [listRuns, status])

  return (
    <Page
      back={{ to: '/ui/agents', label: 'Agents' }}
      title="Agent runs"
      actions={<ButtonLink to="/ui/agents/runs/new" variant="primary">New ad-hoc run</ButtonLink>}
    >
      <Toolbar>
        <ToggleGroup label="Filter by status" options={FILTERS.map(f => ({ value: f, label: f }))} value={status} onChange={setStatus} />
      </Toolbar>
      {error && <Notice tone="error">{error}</Notice>}
      {runs === null && !error && <LoadingState message="Loading runs..." />}
      {runs?.length === 0 && <EmptyState message="No runs" />}
      {!!runs?.length && (
        <List>
          {runs.map(r => (
            <Row
              key={r.id}
              to={`/ui/agents/runs/${r.id}`}
              title={<>{r.starred && <span aria-label="Starred">★ </span>}{r.input}</>}
              badges={<StatusBadge status={r.status} />}
              meta={[<span className="font-mono">{r.model}</span>, formatRelativeTime(r.created_at), `$${r.cost_usd}`]}
            />
          ))}
        </List>
      )}
    </Page>
  )
}

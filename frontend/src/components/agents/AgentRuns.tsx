import { useCallback, useEffect, useState } from 'react'
import { useAgents, type AgentRun, type RunStatus } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { STATUS_LABELS, StatusBadge, TRIGGER_LABELS } from './StatusBadge'
import { ButtonLink, EmptyState, ErrorState, List, LoadingState, Page, RefreshButton, Row, ToggleGroup, Toolbar } from '@/components/shared/ui'
import { formatCost } from '@/utils/format'

const LIMIT = 100

const FILTERS: { value: RunStatus | 'all'; label: string }[] = [
  { value: 'all', label: 'All' },
  ...(['pending', 'running', 'done', 'failed', 'budget_exceeded', 'cancelled'] as const).map(s => ({ value: s, label: STATUS_LABELS[s] })),
]

export default function AgentRuns() {
  const { listRuns, listDefinitions } = useAgents()
  const [status, setStatus] = useState<RunStatus | 'all'>('all')
  const [runs, setRuns] = useState<AgentRun[] | null>(null)
  const [agentNames, setAgentNames] = useState<Map<number, string>>(new Map())
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setError(null)
    listRuns({ status: status === 'all' ? undefined : status, limit: LIMIT }).then(setRuns).catch(e => setError(e.message))
  }, [listRuns, status])

  useEffect(() => {
    setRuns(null)
    load()
  }, [load])

  // Names are a nicety: without them a run is titled by its id.
  useEffect(() => {
    listDefinitions().then(defs => setAgentNames(new Map(defs.map(d => [d.id, d.name])))).catch(() => {})
  }, [listDefinitions])

  const runTitle = (r: AgentRun) => r.definition_id === null
    ? 'Ad-hoc run'
    : agentNames.get(r.definition_id) ?? `Agent ${r.definition_id}`

  return (
    <Page
      back={{ to: '/ui/agents', label: 'Agents' }}
      title="Agent runs"
      actions={<ButtonLink to="/ui/agents/runs/new" variant="primary">New ad-hoc run</ButtonLink>}
    >
      <Toolbar actions={<RefreshButton onClick={load} />}>
        <ToggleGroup label="Filter by status" options={FILTERS} value={status} onChange={setStatus} />
      </Toolbar>
      {error && <ErrorState message={error} onRetry={load} />}
      {runs === null && !error && <LoadingState message="Loading runs..." />}
      {runs?.length === 0 && <EmptyState message="No runs" />}
      {!!runs?.length && (
        <List>
          {runs.map(r => (
            <Row
              key={r.id}
              to={`/ui/agents/runs/${r.id}`}
              title={<>{r.starred && <span aria-label="Starred">★ </span>}{runTitle(r)}</>}
              badges={<StatusBadge status={r.status} />}
              meta={[<span className="font-mono">{r.model}</span>, formatRelativeTime(r.created_at), formatCost(r.cost_usd), TRIGGER_LABELS[r.trigger_kind] ?? r.trigger_kind]}
            >
              <p className="text-slate-600 line-clamp-2 whitespace-pre-line break-words">{r.input}</p>
            </Row>
          ))}
        </List>
      )}
      {runs?.length === LIMIT && <p className="text-sm text-slate-600">Showing the latest {LIMIT} runs.</p>}
    </Page>
  )
}

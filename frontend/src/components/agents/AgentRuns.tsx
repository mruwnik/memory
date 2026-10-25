import { useCallback, useEffect, useRef, useState } from 'react'
import { useAgents, type AgentRun, type RunStatus } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { STATUS_LABELS, StatusBadge, TRIGGER_LABELS } from './StatusBadge'
import { Button, ButtonLink, EmptyState, ErrorState, List, LoadingState, Page, RefreshButton, Row, ToggleGroup, Toolbar } from '@/components/shared/ui'
import { formatCost } from '@/utils/format'

const LIMIT = 100 // also the page size: a full page means there may be older runs

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
  const [more, setMore] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const filter = status === 'all' ? undefined : status

  // Bumped by every page-1 load, so a "Load more" that outlives its filter or a refresh is dropped.
  const generation = useRef(0)
  const [moreError, setMoreError] = useState<string | null>(null)

  const load = useCallback(() => {
    const mine = ++generation.current
    setError(null)
    setMoreError(null)
    setLoadingMore(false)
    listRuns({ status: filter, limit: LIMIT })
      .then(page => {
        if (mine !== generation.current) return
        setRuns(page)
        setMore(page.length === LIMIT)
      })
      .catch(e => { if (mine === generation.current) setError(e.message) })
  }, [listRuns, filter])

  const loadMore = () => {
    const last = runs?.[runs.length - 1]
    if (!last?.created_at) return
    const mine = generation.current
    setLoadingMore(true)
    setMoreError(null)
    listRuns({ status: filter, limit: LIMIT, before: { createdAt: last.created_at, id: last.id } })
      .then(page => {
        if (mine !== generation.current) return
        setRuns(held => [...(held ?? []), ...page])
        setMore(page.length === LIMIT)
        setLoadingMore(false)
      })
      .catch(e => {
        if (mine !== generation.current) return
        setMoreError(e.message)
        setLoadingMore(false)
      })
  }

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
      {moreError && <p role="alert" className="text-red-700">Couldn't load older runs: {moreError}</p>}
      {more && <Button onClick={loadMore} disabled={loadingMore}>{loadingMore ? 'Loading...' : moreError ? 'Retry' : 'Load more'}</Button>}
    </Page>
  )
}

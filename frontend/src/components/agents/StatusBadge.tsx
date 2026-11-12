import type { AgentRun, RunStatus } from '@/hooks/useAgents'
import { Badge, type Tone } from '@/components/shared/ui'
import { formatUserDateTime } from '@/utils/timezones'

const TONES: Record<RunStatus, Tone> = {
  pending: 'warning',
  running: 'info',
  done: 'success',
  failed: 'danger',
  budget_exceeded: 'warning',
  cancelled: 'neutral',
}

export const STATUS_LABELS: Record<RunStatus, string> = {
  pending: 'Pending',
  running: 'Running',
  done: 'Done',
  failed: 'Failed',
  budget_exceeded: 'Over budget',
  cancelled: 'Cancelled',
}

export const TRIGGER_LABELS: Record<string, string> = {
  manual: 'Manual',
  mcp: 'MCP client',
  cron: 'Schedule',
  timer: 'Timer',
  ingest: 'New items',
  raw: 'Webhook event',
  agent: 'Another run',
}

export function StatusBadge({ status }: { status: RunStatus }) {
  return <Badge tone={TONES[status]}>{STATUS_LABELS[status]}</Badge>
}

/** A definition's runs by its name, a prebuilt agent's by slug, a queue delivery by the subscription that routed it. */
export function runTitle(run: AgentRun, agentName?: string | null): string {
  if (run.definition_id !== null) return agentName ?? `Agent ${run.definition_id}`
  if (run.agent) return run.agent
  if (!run.queue) return 'Ad-hoc run'
  const subscription = run.subscription_name ?? (run.subscription_id === null ? null : `Subscription ${run.subscription_id}`)
  return subscription ? `${subscription} → queue ${run.queue}` : `Queue ${run.queue}`
}

/** Where a live worker run stands; null for server runs and ended ones. */
export function workerStatus(run: AgentRun, now = Date.now()): string | null {
  if (run.executor !== 'workers') return null
  if (run.status === 'pending') return 'Waiting for a worker'
  if (run.status !== 'running') return null
  if (!run.lease_expires_at) return 'Held by a worker'
  const lapsed = Date.parse(run.lease_expires_at) <= now
  return `Held by a worker, lease ${lapsed ? 'expired' : 'until'} ${formatUserDateTime(run.lease_expires_at)}`
}

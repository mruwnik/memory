import type { RunStatus } from '@/hooks/useAgents'
import { Badge, type Tone } from '@/components/shared/ui'

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
  ingest: 'New items',
  system: 'System',
}

export function StatusBadge({ status }: { status: RunStatus }) {
  return <Badge tone={TONES[status]}>{STATUS_LABELS[status]}</Badge>
}

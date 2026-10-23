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

export function StatusBadge({ status }: { status: RunStatus }) {
  return <Badge tone={TONES[status]}>{status}</Badge>
}

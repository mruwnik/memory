import type { ServerStatus } from '@/hooks/useAgents'
import { Badge, type Tone } from '@/components/shared/ui'

const LABELS: Record<ServerStatus, [string, Tone]> = {
  connected: ['Connected', 'success'],
  needs_reauth: ['Needs re-auth', 'warning'],
  error: ['Error', 'danger'],
  pending_auth: ['Pending', 'neutral'],
}

export function ServerStatusBadge({ status }: { status: ServerStatus }) {
  const [label, tone] = LABELS[status]
  return <Badge tone={tone}>{label}</Badge>
}

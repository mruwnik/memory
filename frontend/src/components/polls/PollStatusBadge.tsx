import { Badge, type Tone } from '@/components/shared/ui'

const TONES: Record<string, Tone> = { open: 'success', closed: 'neutral', finalized: 'info', cancelled: 'danger' }

export const PollStatusBadge = ({ status }: { status: string }) => (
  <Badge className="capitalize" tone={TONES[status] ?? 'neutral'}>{status}</Badge>
)

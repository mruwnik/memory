import type { ServerStatus } from '@/hooks/useAgents'

const LABELS: Record<ServerStatus, [string, string]> = {
  connected: ['Connected', 'bg-green-100 text-green-700'],
  needs_reauth: ['Needs re-auth', 'bg-amber-100 text-amber-800'],
  error: ['Error', 'bg-red-100 text-red-700'],
  pending_auth: ['Pending', 'bg-slate-100 text-slate-600'],
}

export function ServerStatusBadge({ status }: { status: ServerStatus }) {
  const [label, color] = LABELS[status]
  return <span className={`px-2 py-0.5 rounded text-xs ${color}`}>{label}</span>
}

import type { RunStatus } from '@/hooks/useAgents'

const COLORS: Record<RunStatus, string> = {
  pending: 'bg-yellow-100 text-yellow-700',
  running: 'bg-blue-100 text-blue-700',
  done: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  budget_exceeded: 'bg-orange-100 text-orange-700',
  cancelled: 'bg-slate-100 text-slate-500',
}

export function StatusBadge({ status }: { status: RunStatus }) {
  return <span className={`px-2 py-0.5 rounded text-xs ${COLORS[status]}`}>{status}</span>
}

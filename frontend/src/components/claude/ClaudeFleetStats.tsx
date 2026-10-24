import { useState, useEffect, useCallback, useMemo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from 'recharts'
import {
  useClaude,
  type FleetStats,
  type ContainerStatsEntry,
  type StatsHistoryPoint,
  type Environment,
} from '../../hooks/useClaude'
import { useUsers, type User } from '../../hooks/useUsers'
import { Badge, Card, EmptyState, LoadingState, Notice, Section, Table, cx } from '@/components/shared/ui'

const REFRESH_MS = 10_000
const HISTORY_MAX = 240 // ~2h at 30s cadence

export const formatMb = (mb: number): string => {
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`
  return `${Math.round(mb)} MB`
}

// The level is spelled out as well as coloured, so it doesn't rely on colour vision.
export const Usage = ({ pct, children }: { pct: number; children: ReactNode }) => {
  const level = pct >= 90 ? 'critical' : pct >= 75 ? 'high' : null
  return (
    <span className={cx('font-mono', level === 'critical' ? 'text-red-700 font-semibold' : level === 'high' ? 'text-amber-800 font-semibold' : 'text-slate-700')}>
      {children}{level && <span className="font-sans"> ({level})</span>}
    </span>
  )
}

const barWidth = (value: number, max: number): string => {
  if (max <= 0) return '0%'
  return `${Math.min(100, (value / max) * 100).toFixed(1)}%`
}

// Session IDs follow `u<userId>-<source><sourceId?>-<randomHex>`. `source` is
// `e<envId>` (environment), `s<snapId>` (snapshot), or bare `x` (no source).
// Returns null if the id doesn't parse — callers fall back to the raw id.
export interface ParsedSessionId {
  userId: number | null
  sourceType: 'environment' | 'snapshot' | 'unknown'
  sourceId: number | null
  hex: string
}

export const parseSessionId = (sessionId: string): ParsedSessionId | null => {
  const parts = sessionId.split('-')
  if (parts.length !== 3) return null
  const [userPart, sourcePart, hex] = parts
  if (!userPart.startsWith('u')) return null
  const userId = Number(userPart.slice(1))
  if (Number.isNaN(userId)) return null

  let sourceType: ParsedSessionId['sourceType'] = 'unknown'
  let sourceId: number | null = null
  if (sourcePart.startsWith('e')) {
    sourceType = 'environment'
    const n = Number(sourcePart.slice(1))
    if (!Number.isNaN(n)) sourceId = n
  } else if (sourcePart.startsWith('s')) {
    sourceType = 'snapshot'
    const n = Number(sourcePart.slice(1))
    if (!Number.isNaN(n)) sourceId = n
  }
  return { userId, sourceType, sourceId, hex }
}

export interface SessionDisplay {
  // Primary label: env name if known, else `snapshot #N`, else fallback.
  title: string
  // Secondary label: user name if known, else `user N`. null if no info.
  subtitle: string | null
}

export const sessionDisplay = (
  sessionId: string,
  environments: Environment[],
  users: User[]
): SessionDisplay => {
  const parsed = parseSessionId(sessionId)
  if (!parsed) return { title: sessionId, subtitle: null }

  let title = sessionId
  if (parsed.sourceType === 'environment' && parsed.sourceId !== null) {
    const env = environments.find((e) => e.id === parsed.sourceId)
    title = env ? env.name : `env #${parsed.sourceId}`
  } else if (parsed.sourceType === 'snapshot' && parsed.sourceId !== null) {
    title = `snapshot #${parsed.sourceId}`
  }

  let subtitle: string | null = null
  if (parsed.userId !== null) {
    const user = users.find((u) => u.id === parsed.userId)
    subtitle = user ? user.name : `user ${parsed.userId}`
  }
  return { title, subtitle }
}

const Bar: React.FC<{
  used: number
  allocated: number
  max: number
  unit: string
  format?: (n: number) => string
}> = ({ used, allocated, max, unit, format }) => {
  const fmt = format ?? ((n) => n.toString())
  return (
    <div>
      <div className="flex items-baseline justify-between text-xs mb-1">
        <span className="font-medium text-slate-600">
          used <Usage pct={(used / Math.max(max, 1)) * 100}>{fmt(used)}</Usage>
        </span>
        <span className="font-mono text-slate-500">
          allocated {fmt(allocated)} / {fmt(max)} {unit}
        </span>
      </div>
      <div className="relative h-2 bg-slate-100 rounded overflow-hidden">
        <div
          className="absolute top-0 left-0 h-2 bg-slate-300"
          style={{ width: barWidth(allocated, max) }}
        />
        <div
          className="absolute top-0 left-0 h-2 bg-blue-500"
          style={{ width: barWidth(used, max) }}
        />
      </div>
    </div>
  )
}

const GlobalSummary: React.FC<{ snap: FleetStats }> = ({ snap }) => {
  if (!snap.global) return null
  const { running, max, memory_mb, cpus } = snap.global
  return (
    <Card title="Cluster" actions={<span className="text-xs text-slate-600 font-mono">{running} / {max} containers</span>}>
      <div className="space-y-3">
        <Bar
          used={cpus.used}
          allocated={cpus.allocated}
          max={cpus.max}
          unit="CPUs"
          format={(n) => n.toFixed(2)}
        />
        <Bar
          used={memory_mb.used}
          allocated={memory_mb.allocated}
          max={memory_mb.max}
          unit=""
          format={formatMb}
        />
      </div>
    </Card>
  )
}

const StatusPill: React.FC<{ status: string }> = ({ status }) => (
  <Badge tone={status === 'running' ? 'success' : status === 'exited' ? 'neutral' : 'warning'}>{status}</Badge>
)

const ContainerRow: React.FC<{
  c: ContainerStatsEntry
  display: SessionDisplay
  owned: boolean
}> = ({ c, display, owned }) => {
  const used = c.used
  return (
    <tr>
      <td className="px-3 py-2">
        <div className="flex flex-wrap items-center gap-2 text-sm text-slate-800">
          {owned ? <Link to={`/ui/claude/${c.id}`} className="text-primary hover:underline">{display.title}</Link> : display.title}
          {!owned && <Badge>Not yours</Badge>}
        </div>
        {display.subtitle && (
          <div className="text-xs text-slate-500">{display.subtitle}</div>
        )}
        <div className="font-mono text-[10px] text-slate-500">{c.id}</div>
      </td>
      <td className="px-3 py-2"><StatusPill status={c.status} /></td>
      <td className="px-3 py-2 text-xs font-mono text-right">
        {used && used.cpu_pct !== null
          ? <Usage pct={(used.cpu_pct / Math.max(c.allocated.cpus * 100, 1)) * 100}>{used.cpu_pct.toFixed(1)}%</Usage>
          : <span className="text-slate-500">—</span>}
        <span className="text-slate-500"> / {(c.allocated.cpus * 100).toFixed(0)}%</span>
      </td>
      <td className="px-3 py-2 text-xs font-mono text-right">
        {used
          ? <>
              <Usage pct={used.memory_pct}>{formatMb(used.memory_mb)}</Usage>
              <span className="text-slate-500"> / {formatMb(c.allocated.memory_mb)} ({used.memory_pct.toFixed(0)}%)</span>
            </>
          : <span className="text-slate-500">—</span>}
      </td>
    </tr>
  )
}

const formatTickTime = (ms: number): string => {
  const d = new Date(ms)
  const hh = d.getHours().toString().padStart(2, '0')
  const mm = d.getMinutes().toString().padStart(2, '0')
  return `${hh}:${mm}`
}

export const HistoryChart: React.FC<{ points: StatsHistoryPoint[] }> = ({ points }) => {
  const data = useMemo(
    () => points.map((p) => ({
      ts: new Date(p.ts).getTime(),
      cpu: p.cpu_pct,
      mem: p.memory_mb,
    })),
    [points]
  )

  if (data.length === 0) {
    return (
      <div className="text-center text-sm text-slate-500 py-8">
        No history yet. Sampler ticks every 30s.
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div>
        <div className="text-xs font-medium text-slate-600 mb-1">CPU (% of one core)</div>
        <div style={{ height: 140 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#e2e8f0" strokeDasharray="2 2" />
              <XAxis
                dataKey="ts"
                type="number"
                domain={['dataMin', 'dataMax']}
                tickFormatter={formatTickTime}
                tick={{ fontSize: 10, fill: '#64748b' }}
                stroke="#cbd5e1"
              />
              <YAxis
                tick={{ fontSize: 10, fill: '#64748b' }}
                stroke="#cbd5e1"
                tickFormatter={(v) => `${v}%`}
                width={40}
              />
              <Tooltip
                labelFormatter={(v: number) => new Date(v).toLocaleTimeString()}
                formatter={(v: number) => `${v.toFixed(2)}%`}
                contentStyle={{ fontSize: 12 }}
              />
              <Line
                type="monotone"
                dataKey="cpu"
                stroke="#3b82f6"
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div>
        <div className="text-xs font-medium text-slate-600 mb-1">Memory (MB)</div>
        <div style={{ height: 140 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#e2e8f0" strokeDasharray="2 2" />
              <XAxis
                dataKey="ts"
                type="number"
                domain={['dataMin', 'dataMax']}
                tickFormatter={formatTickTime}
                tick={{ fontSize: 10, fill: '#64748b' }}
                stroke="#cbd5e1"
              />
              <YAxis
                tick={{ fontSize: 10, fill: '#64748b' }}
                stroke="#cbd5e1"
                width={50}
              />
              <Tooltip
                labelFormatter={(v: number) => new Date(v).toLocaleTimeString()}
                formatter={(v: number) => `${v} MB`}
                contentStyle={{ fontSize: 12 }}
              />
              <Line
                type="monotone"
                dataKey="mem"
                stroke="#10b981"
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  )
}

// SessionStatsPanel — live stats + history for a single session.
// Used in the session details view. Auto-refreshes the history; the live
// numbers come from the snapshot the parent already fetches and passes in.
export const SessionStatsPanel: React.FC<{ sessionId: string }> = ({ sessionId }) => {
  const { getStatsHistory } = useClaude()
  const [points, setPoints] = useState<StatsHistoryPoint[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const h = await getStatsHistory({ sessionId, max: HISTORY_MAX })
      setPoints(h.points)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load history')
    } finally {
      setLoading(false)
    }
  }, [sessionId, getStatsHistory])

  useEffect(() => {
    setLoading(true)
    load()
    const id = setInterval(load, REFRESH_MS)
    return () => clearInterval(id)
  }, [load])

  if (loading && points.length === 0) {
    return <div className="text-sm text-slate-500 py-4">Loading history…</div>
  }
  if (error) return <Notice tone="error" onRetry={load}>{error}</Notice>
  return <HistoryChart points={points} />
}

interface ClaudeFleetStatsProps {
  // Lookup tables: parent loads these once; we render with what's available.
  // Empty arrays are fine — display falls back to ids.
  environments: Environment[]
  hasAdminScope: boolean
  // The viewer's user_id. Rows whose session_id parses to a different user
  // aren't linked, since /claude/list only returns the caller's
  // own sessions — clicking would land on an empty details panel. Null
  // means we don't know yet (auth still loading); be permissive in that
  // case so we don't accidentally lock everything out.
  currentUserId: number | null
}

const ClaudeFleetStats: React.FC<ClaudeFleetStatsProps> = ({
  environments,
  hasAdminScope,
  currentUserId,
}) => {
  const { getFleetStats } = useClaude()
  const { listUsers } = useUsers()
  const [snap, setSnap] = useState<FleetStats | null>(null)
  const [users, setUsers] = useState<User[]>([])
  const [error, setError] = useState<string | null>(null)

  const loadSnap = useCallback(async () => {
    try {
      const s = await getFleetStats()
      setSnap(s)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load stats')
    }
  }, [getFleetStats])

  useEffect(() => {
    loadSnap()
    const id = setInterval(loadSnap, REFRESH_MS)
    return () => clearInterval(id)
  }, [loadSnap])

  // User listing requires admin scope server-side. Skip the call entirely for
  // non-admins — they'd get 403 anyway, and they only ever see their own
  // sessions in the table, so the lookup is moot.
  useEffect(() => {
    if (!hasAdminScope) return
    listUsers()
      .then(setUsers)
      .catch(() => {
        // Non-fatal: rows just show "user N" instead of names.
      })
  }, [hasAdminScope, listUsers])

  if (error) return <Notice tone="error" onRetry={loadSnap}>{error}</Notice>
  if (!snap) {
    return (
      <LoadingState message="Loading fleet stats…" />
    )
  }

  const containers = snap.containers
  return (
    <div className="space-y-4">
      {snap.global && <GlobalSummary snap={snap} />}

      <Section title={<>Containers <span className="text-slate-500 font-normal">({containers.length})</span></>}
        actions={<span className="text-xs text-slate-600 font-mono">updated {new Date(snap.ts).toLocaleTimeString()}</span>}>
        {containers.length === 0 ? (
          <EmptyState message="No containers running." />
        ) : (
          <Table aria-label="Running containers">
            <thead>
              <tr>
                <th>Session</th>
                <th>Status</th>
                <th className="text-right">CPU</th>
                <th className="text-right">Memory</th>
              </tr>
            </thead>
            <tbody>
              {containers.map((c) => {
                const parsed = parseSessionId(c.id)
                const owned =
                  currentUserId === null ||
                  parsed === null ||
                  parsed.userId === currentUserId
                return (
                  <ContainerRow
                    key={c.id}
                    c={c}
                    display={sessionDisplay(c.id, environments, users)}
                    owned={owned}
                  />
                )
              })}
            </tbody>
          </Table>
        )}
      </Section>
    </div>
  )
}

export default ClaudeFleetStats

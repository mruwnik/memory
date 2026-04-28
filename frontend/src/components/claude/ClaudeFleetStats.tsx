import { useState, useEffect, useCallback, useMemo } from 'react'
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
} from '../../hooks/useClaude'

const REFRESH_MS = 10_000
const HISTORY_MAX = 240 // ~2h at 30s cadence

const formatMb = (mb: number): string => {
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`
  return `${Math.round(mb)} MB`
}

const usagePctColor = (pct: number): string => {
  if (pct >= 90) return 'text-red-600'
  if (pct >= 75) return 'text-yellow-600'
  return 'text-slate-700'
}

// Bar width helper: clamps to 100% so over-allocation (allocated > max) at
// least visualizes as "full" rather than overflowing the track.
const barWidth = (value: number, max: number): string => {
  if (max <= 0) return '0%'
  return `${Math.min(100, (value / max) * 100).toFixed(1)}%`
}

const Bar: React.FC<{
  used: number
  allocated: number
  max: number
  unit: string
  format?: (n: number) => string
}> = ({ used, allocated, max, unit, format }) => {
  const fmt = format ?? ((n) => n.toString())
  // Two stacked layers: a wider faded bar showing allocation, a narrower
  // solid bar showing live usage. Quick visual answer to "are we
  // overcommitted?" (allocated bar near full) vs. "are we busy?" (used bar
  // catching up to allocated).
  return (
    <div>
      <div className="flex items-baseline justify-between text-xs mb-1">
        <span className="font-medium text-slate-600">
          used <span className={`font-mono ${usagePctColor((used / Math.max(max, 1)) * 100)}`}>{fmt(used)}</span>
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
    <div className="bg-white rounded-lg border border-slate-200 p-4">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-sm font-semibold text-slate-700">Cluster</h3>
        <span className="text-xs text-slate-500 font-mono">
          {running} / {max} containers
        </span>
      </div>
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
    </div>
  )
}

const StatusPill: React.FC<{ status: string }> = ({ status }) => {
  const cls =
    status === 'running'
      ? 'bg-green-100 text-green-700'
      : status === 'exited'
        ? 'bg-slate-100 text-slate-600'
        : 'bg-yellow-100 text-yellow-700'
  return (
    <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${cls}`}>
      {status}
    </span>
  )
}

const ContainerRow: React.FC<{
  c: ContainerStatsEntry
  selected: boolean
  onSelect: () => void
}> = ({ c, selected, onSelect }) => {
  const used = c.used
  return (
    <tr
      className={`border-b border-slate-100 cursor-pointer transition-colors ${
        selected ? 'bg-blue-50' : 'hover:bg-slate-50'
      }`}
      onClick={onSelect}
    >
      <td className="px-3 py-2 font-mono text-xs text-slate-700">{c.id}</td>
      <td className="px-3 py-2"><StatusPill status={c.status} /></td>
      <td className="px-3 py-2 text-xs font-mono text-right">
        {used && used.cpu_pct !== null
          ? <span className={usagePctColor((used.cpu_pct / Math.max(c.allocated.cpus * 100, 1)) * 100)}>
              {used.cpu_pct.toFixed(1)}%
            </span>
          : <span className="text-slate-400">—</span>}
        <span className="text-slate-400"> / {(c.allocated.cpus * 100).toFixed(0)}%</span>
      </td>
      <td className="px-3 py-2 text-xs font-mono text-right">
        {used
          ? <>
              <span className={usagePctColor(used.memory_pct)}>{formatMb(used.memory_mb)}</span>
              <span className="text-slate-400"> / {formatMb(c.allocated.memory_mb)} ({used.memory_pct.toFixed(0)}%)</span>
            </>
          : <span className="text-slate-400">—</span>}
      </td>
    </tr>
  )
}

// Recharts wants timestamps as numbers/strings on the X axis. We feed millis
// for proper time-axis behavior and format the tick labels ourselves.
const formatTickTime = (ms: number): string => {
  const d = new Date(ms)
  const hh = d.getHours().toString().padStart(2, '0')
  const mm = d.getMinutes().toString().padStart(2, '0')
  return `${hh}:${mm}`
}

const HistoryChart: React.FC<{ points: StatsHistoryPoint[] }> = ({ points }) => {
  // One row per point, with cpu_pct (% of one core) and memory_mb so the two
  // y-axes can share the same domain. Recharts skips null cpu_pct gaps
  // automatically with `connectNulls={false}` (the default).
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

const ClaudeFleetStats: React.FC = () => {
  const { getFleetStats, getStatsHistory } = useClaude()
  const [snap, setSnap] = useState<FleetStats | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [history, setHistory] = useState<StatsHistoryPoint[]>([])
  const [historyError, setHistoryError] = useState<string | null>(null)

  const loadSnap = useCallback(async () => {
    try {
      const s = await getFleetStats()
      setSnap(s)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load stats')
    }
  }, [getFleetStats])

  const loadHistory = useCallback(
    async (sessionId: string) => {
      try {
        const h = await getStatsHistory({ sessionId, max: HISTORY_MAX })
        setHistory(h.points)
        setHistoryError(null)
      } catch (e) {
        setHistoryError(e instanceof Error ? e.message : 'Failed to load history')
        setHistory([])
      }
    },
    [getStatsHistory]
  )

  // Initial + periodic snapshot refresh.
  useEffect(() => {
    loadSnap()
    const id = setInterval(loadSnap, REFRESH_MS)
    return () => clearInterval(id)
  }, [loadSnap])

  // Load history when a container is selected, and refresh on the same
  // cadence as the snapshot. Each refresh re-fetches the full window — at 240
  // points × ~50 bytes ≈ 12KB per call, this is cheap, and avoids the
  // bookkeeping of incremental `since=` polling.
  useEffect(() => {
    if (!selected) {
      setHistory([])
      return
    }
    loadHistory(selected)
    const id = setInterval(() => loadHistory(selected), REFRESH_MS)
    return () => clearInterval(id)
  }, [selected, loadHistory])

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">
        {error}
      </div>
    )
  }

  if (!snap) {
    return (
      <div className="bg-white rounded-lg border border-slate-200 p-4 text-sm text-slate-500">
        Loading fleet stats…
      </div>
    )
  }

  const containers = snap.containers
  const selectedContainer = selected
    ? containers.find((c) => c.id === selected) ?? null
    : null

  return (
    <div className="space-y-4">
      {snap.global && <GlobalSummary snap={snap} />}

      <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
        <div className="px-4 py-2 border-b border-slate-200 flex items-baseline justify-between">
          <h3 className="text-sm font-semibold text-slate-700">
            Containers <span className="text-slate-400 font-normal">({containers.length})</span>
          </h3>
          <span className="text-xs text-slate-500 font-mono">
            updated {new Date(snap.ts).toLocaleTimeString()}
          </span>
        </div>
        {containers.length === 0 ? (
          <div className="px-4 py-6 text-sm text-slate-500 text-center">
            No containers running.
          </div>
        ) : (
          <table className="w-full">
            <thead className="bg-slate-50 text-left">
              <tr className="text-xs text-slate-500 uppercase tracking-wider">
                <th className="px-3 py-2 font-medium">Session</th>
                <th className="px-3 py-2 font-medium">Status</th>
                <th className="px-3 py-2 font-medium text-right">CPU</th>
                <th className="px-3 py-2 font-medium text-right">Memory</th>
              </tr>
            </thead>
            <tbody>
              {containers.map((c) => (
                <ContainerRow
                  key={c.id}
                  c={c}
                  selected={selected === c.id}
                  onSelect={() => setSelected(selected === c.id ? null : c.id)}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {selectedContainer && (
        <div className="bg-white rounded-lg border border-slate-200 p-4">
          <div className="flex items-baseline justify-between mb-3">
            <h3 className="text-sm font-semibold text-slate-700">
              History <span className="font-mono text-slate-500 font-normal">— {selectedContainer.id}</span>
            </h3>
            <button
              onClick={() => setSelected(null)}
              className="text-xs text-slate-500 hover:text-slate-700"
            >
              close
            </button>
          </div>
          {historyError ? (
            <div className="text-sm text-red-600">{historyError}</div>
          ) : (
            <HistoryChart points={history} />
          )}
        </div>
      )}
    </div>
  )
}

export default ClaudeFleetStats

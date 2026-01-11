import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import {
  useMetrics,
  MetricsSummaryResponse,
  SystemMetricsResponse,
  TaskMetricsResponse,
  McpMetricsResponse,
  TimeRange,
  timeRangeToHours,
} from '@/hooks/useMetrics'
import { SummaryCards } from './SummaryCards'
import { SystemHealthChart } from './SystemHealthChart'
import { TaskDurationChart } from './TaskDurationChart'
import { McpUsageChart } from './McpUsageChart'

// System metrics are sampled minutely - 7 days max keeps memory reasonable
const SYSTEM_METRICS_MAX_HOURS = 168
const TABLE_DISPLAY_LIMIT = 25

type RefreshInterval = 'off' | '10s' | '30s' | '1m' | '5m'

const refreshIntervalToMs = (interval: RefreshInterval): number | null => {
  switch (interval) {
    case '10s': return 10000
    case '30s': return 30000
    case '1m': return 60000
    case '5m': return 300000
    default: return null
  }
}

const Metrics: React.FC = () => {
  const [timeRange, setTimeRange] = useState<TimeRange>('24h')
  const [refreshInterval, setRefreshInterval] = useState<RefreshInterval>('off')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)

  // Data state
  const [summary, setSummary] = useState<MetricsSummaryResponse | null>(null)
  const [systemData, setSystemData] = useState<SystemMetricsResponse | null>(null)
  const [taskData, setTaskData] = useState<TaskMetricsResponse | null>(null)
  const [mcpData, setMcpData] = useState<McpMetricsResponse | null>(null)

  const { getSummary, getSystemMetrics, getTaskMetrics, getMcpMetrics } = useMetrics()

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)

    const hours = timeRangeToHours(timeRange)

    try {
      const [summaryRes, systemRes, taskRes, mcpRes] = await Promise.all([
        getSummary(hours),
        getSystemMetrics(Math.min(hours, SYSTEM_METRICS_MAX_HOURS)),
        getTaskMetrics(hours, undefined, TABLE_DISPLAY_LIMIT),
        getMcpMetrics(hours, undefined, TABLE_DISPLAY_LIMIT),
      ])

      setSummary(summaryRes)
      setSystemData(systemRes)
      setTaskData(taskRes)
      setMcpData(mcpRes)
      setLastRefresh(new Date())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load metrics')
    } finally {
      setLoading(false)
    }
  }, [timeRange, getSummary, getSystemMetrics, getTaskMetrics, getMcpMetrics])

  useEffect(() => {
    loadData()
  }, [loadData])

  // Auto-refresh effect
  useEffect(() => {
    const ms = refreshIntervalToMs(refreshInterval)
    if (!ms) return

    const intervalId = setInterval(() => {
      loadData()
    }, ms)

    return () => clearInterval(intervalId)
  }, [refreshInterval, loadData])

  // Calculate summary stats
  const totalEvents = summary?.metrics.reduce((sum, m) => sum + m.count, 0) ?? 0
  const successCount = summary?.metrics.reduce((sum, m) => sum + m.success_count, 0) ?? 0
  const failureCount = summary?.metrics.reduce((sum, m) => sum + m.failure_count, 0) ?? 0
  const successRate = totalEvents > 0 ? Math.round((successCount / totalEvents) * 100) : 0

  // Calculate average duration across all timed metrics
  const timedMetrics = summary?.metrics.filter(m => m.avg_duration_ms !== null) ?? []
  const avgDuration = timedMetrics.length > 0
    ? Math.round(timedMetrics.reduce((sum, m) => sum + (m.avg_duration_ms ?? 0), 0) / timedMetrics.length)
    : 0

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <header className="flex flex-wrap items-center gap-4 mb-6 pb-4 border-b border-slate-200">
        <div className="flex items-center gap-4 flex-1">
          <Link to="/ui/dashboard" className="text-primary hover:underline">&larr; Dashboard</Link>
          <h1 className="text-2xl font-semibold text-slate-800">System Metrics</h1>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <TimeRangeSelector value={timeRange} onChange={setTimeRange} />
          <RefreshIntervalSelector value={refreshInterval} onChange={setRefreshInterval} />
          <button
            onClick={loadData}
            className="bg-primary text-white py-2 px-4 rounded-lg hover:bg-primary-dark disabled:bg-slate-400"
            disabled={loading}
          >
            {loading ? 'Loading...' : 'Refresh'}
          </button>
          {lastRefresh && (
            <span className="text-sm text-slate-500">
              Updated {formatTimestamp(lastRefresh.toISOString())}
            </span>
          )}
        </div>
      </header>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg mb-6 flex justify-between items-center">
          <p>{error}</p>
          <button onClick={loadData} className="text-primary hover:underline">Retry</button>
        </div>
      )}

      {loading && !summary && (
        <div className="text-center py-12 text-slate-500">
          <p>Loading metrics...</p>
        </div>
      )}

      {summary && (
        <>
          <section className="mb-8">
            <SummaryCards
              totalEvents={totalEvents}
              successRate={successRate}
              avgDuration={avgDuration}
              systemMetrics={systemData?.latest ?? {}}
            />
          </section>

          <section className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
            <div className="bg-white p-6 rounded-xl shadow-md lg:col-span-2">
              <h3 className="text-lg font-semibold text-slate-800 mb-4">System Health</h3>
              {systemData && <SystemHealthChart data={systemData} />}
            </div>

            <div className="bg-white p-6 rounded-xl shadow-md">
              <h3 className="text-lg font-semibold text-slate-800 mb-4">Task Performance</h3>
              {summary && <TaskDurationChart data={summary.metrics.filter(m => m.metric_type === 'task')} />}
            </div>

            <div className="bg-white p-6 rounded-xl shadow-md">
              <h3 className="text-lg font-semibold text-slate-800 mb-4">MCP Tool Usage</h3>
              {summary && <McpUsageChart data={summary.metrics.filter(m => m.metric_type === 'mcp_call')} />}
            </div>
          </section>

          <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="bg-white p-6 rounded-xl shadow-md">
              <h3 className="text-lg font-semibold text-slate-800 mb-4">Recent Task Executions</h3>
              <TaskTable events={taskData?.events ?? []} />
            </div>

            <div className="bg-white p-6 rounded-xl shadow-md">
              <h3 className="text-lg font-semibold text-slate-800 mb-4">Recent MCP Calls</h3>
              <McpTable events={mcpData?.events ?? []} />
            </div>
          </section>
        </>
      )}
    </div>
  )
}

// Time range selector component
interface TimeRangeSelectorProps {
  value: TimeRange
  onChange: (range: TimeRange) => void
}

const TimeRangeSelector: React.FC<TimeRangeSelectorProps> = ({ value, onChange }) => {
  const ranges: { value: TimeRange; label: string }[] = [
    { value: '1h', label: '1 Hour' },
    { value: '6h', label: '6 Hours' },
    { value: '24h', label: '24 Hours' },
    { value: '7d', label: '7 Days' },
  ]

  return (
    <div className="flex gap-1">
      {ranges.map(range => (
        <button
          key={range.value}
          className={`py-1.5 px-3 rounded text-sm font-medium transition-colors ${
            value === range.value
              ? 'bg-primary text-white'
              : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-50'
          }`}
          onClick={() => onChange(range.value)}
        >
          {range.label}
        </button>
      ))}
    </div>
  )
}

// Refresh interval selector component
interface RefreshIntervalSelectorProps {
  value: RefreshInterval
  onChange: (interval: RefreshInterval) => void
}

const RefreshIntervalSelector: React.FC<RefreshIntervalSelectorProps> = ({ value, onChange }) => {
  const intervals: { value: RefreshInterval; label: string }[] = [
    { value: 'off', label: 'Off' },
    { value: '10s', label: '10s' },
    { value: '30s', label: '30s' },
    { value: '1m', label: '1m' },
    { value: '5m', label: '5m' },
  ]

  return (
    <div className="flex items-center gap-1">
      <span className="text-sm text-slate-500 mr-1">Auto:</span>
      {intervals.map(interval => (
        <button
          key={interval.value}
          className={`py-1.5 px-2 rounded text-sm font-medium transition-colors ${
            value === interval.value
              ? 'bg-primary text-white'
              : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-50'
          }`}
          onClick={() => onChange(interval.value)}
        >
          {interval.label}
        </button>
      ))}
    </div>
  )
}

// Task events table
interface TaskTableProps {
  events: Array<{
    id: number
    timestamp: string
    name: string
    duration_ms: number | null
    status: string | null
  }>
}

const TaskTable: React.FC<TaskTableProps> = ({ events }) => {
  if (events.length === 0) {
    return <p className="text-slate-500 text-center py-4">No task events in this time range</p>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="text-left py-2 px-3 font-medium text-slate-600">Time</th>
            <th className="text-left py-2 px-3 font-medium text-slate-600">Task</th>
            <th className="text-left py-2 px-3 font-medium text-slate-600">Duration</th>
            <th className="text-left py-2 px-3 font-medium text-slate-600">Status</th>
          </tr>
        </thead>
        <tbody>
          {events.map(event => (
            <tr key={event.id} className="border-b border-slate-100 hover:bg-slate-50">
              <td className="py-2 px-3 text-slate-500">{formatTimestamp(event.timestamp)}</td>
              <td className="py-2 px-3 text-slate-800 font-medium truncate max-w-48">{event.name}</td>
              <td className="py-2 px-3 text-slate-600">{event.duration_ms !== null ? `${Math.round(event.duration_ms)}ms` : '-'}</td>
              <td className="py-2 px-3">
                <StatusBadge status={event.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// MCP events table
interface McpTableProps {
  events: Array<{
    id: number
    timestamp: string
    name: string
    duration_ms: number | null
    status: string | null
  }>
}

const McpTable: React.FC<McpTableProps> = ({ events }) => {
  if (events.length === 0) {
    return <p className="text-slate-500 text-center py-4">No MCP calls in this time range</p>
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-200">
            <th className="text-left py-2 px-3 font-medium text-slate-600">Time</th>
            <th className="text-left py-2 px-3 font-medium text-slate-600">Tool</th>
            <th className="text-left py-2 px-3 font-medium text-slate-600">Duration</th>
            <th className="text-left py-2 px-3 font-medium text-slate-600">Status</th>
          </tr>
        </thead>
        <tbody>
          {events.map(event => (
            <tr key={event.id} className="border-b border-slate-100 hover:bg-slate-50">
              <td className="py-2 px-3 text-slate-500">{formatTimestamp(event.timestamp)}</td>
              <td className="py-2 px-3 text-slate-800 font-medium truncate max-w-48">{event.name}</td>
              <td className="py-2 px-3 text-slate-600">{event.duration_ms !== null ? `${Math.round(event.duration_ms)}ms` : '-'}</td>
              <td className="py-2 px-3">
                <StatusBadge status={event.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// Status badge component
const StatusBadge: React.FC<{ status: string | null }> = ({ status }) => {
  const colors: Record<string, string> = {
    success: 'bg-green-100 text-green-700',
    failure: 'bg-red-100 text-red-700',
    error: 'bg-red-100 text-red-700',
    pending: 'bg-yellow-100 text-yellow-700',
  }

  const colorClass = colors[status ?? ''] ?? 'bg-slate-100 text-slate-600'

  return (
    <span className={`${colorClass} px-2 py-0.5 rounded text-xs font-medium`}>
      {status ?? 'unknown'}
    </span>
  )
}

// Utility function for timestamp formatting
const formatTimestamp = (isoString: string): string => {
  const date = new Date(isoString)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)

  if (diffMins < 1) return 'Just now'
  if (diffMins < 60) return `${diffMins}m ago`

  const diffHours = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours}h ago`

  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default Metrics

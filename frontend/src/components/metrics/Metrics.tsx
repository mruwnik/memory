import { useState, useEffect, useCallback } from 'react'
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
import { Button, Card, Notice, Page, ToggleGroup } from '@/components/shared/ui'

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
    <Page
      title="System Metrics"
      actions={<>
        <div className="flex flex-wrap items-center gap-3">
          <TimeRangeSelector value={timeRange} onChange={setTimeRange} />
          <RefreshIntervalSelector value={refreshInterval} onChange={setRefreshInterval} />
          <Button variant="primary"
            onClick={loadData}

            disabled={loading}
          >
            {loading ? 'Loading...' : 'Refresh'}
          </Button>
          {lastRefresh && (
            <span className="text-sm text-slate-500">
              Updated {formatTimestamp(lastRefresh.toISOString())}
            </span>
          )}
        </div>
      </>}
      width="wide"
    >
      {error && (
        <Notice tone="error" onRetry={loadData}>{error}</Notice>
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
            <Card title="System Health" className="lg:col-span-2">
              {systemData && <SystemHealthChart data={systemData} />}
            </Card>

            <Card title="Task Performance">
              {summary && <TaskDurationChart data={summary.metrics.filter(m => m.metric_type === 'task')} />}
            </Card>

            <Card title="MCP Tool Usage">
              {summary && <McpUsageChart data={summary.metrics.filter(m => m.metric_type === 'mcp_call')} />}
            </Card>
          </section>

          <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <Card title="Recent Task Executions">
              <TaskTable events={taskData?.events ?? []} />
            </Card>

            <Card title="Recent MCP Calls">
              <McpTable events={mcpData?.events ?? []} />
            </Card>
          </section>
        </>
      )}
    </Page>
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

  return <ToggleGroup label="Time range" options={ranges} value={value} onChange={onChange} />
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
    <div className="flex items-center gap-2">
      <span className="text-sm text-slate-600">Auto:</span>
      <ToggleGroup label="Auto refresh" options={intervals} value={value} onChange={onChange} />
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

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
import { Badge, Card, CardGrid, LoadingState, Notice, Page, RefreshButton, Table, ToggleGroup, Toolbar, type Tone } from '@/components/shared/ui'

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
      title="Metrics"
      width="wide"
    >
      <Toolbar actions={<>
        {lastRefresh && <span className="text-sm text-slate-600">Updated {formatTimestamp(lastRefresh.toISOString())}</span>}
        <RefreshButton onClick={loadData} loading={loading} label="Refresh metrics" />
      </>}>
        <TimeRangeSelector value={timeRange} onChange={setTimeRange} />
        <RefreshIntervalSelector value={refreshInterval} onChange={setRefreshInterval} />
      </Toolbar>

      {error && <Notice tone="error" onRetry={loadData}>{error}</Notice>}

      {loading && !summary && <LoadingState message="Loading metrics..." />}

      {summary && (
        <>
          <SummaryCards
            totalEvents={totalEvents}
            successRate={successRate}
            avgDuration={avgDuration}
            systemMetrics={systemData?.latest ?? {}}
          />
          <CardGrid>
            <Card title="System health" className="lg:col-span-2">
              {systemData && <SystemHealthChart data={systemData} />}
            </Card>
            <Card title="Task performance">
              <TaskDurationChart data={summary.metrics.filter(m => m.metric_type === 'task')} />
            </Card>
            <Card title="MCP Tool Usage">
              <McpUsageChart data={summary.metrics.filter(m => m.metric_type === 'mcp_call')} />
            </Card>
            <Card title="Recent task executions">
              <EventsTable nameHeader="Task" empty="No task events in this time range" events={taskData?.events ?? []} />
            </Card>
            <Card title="Recent MCP calls">
              <EventsTable nameHeader="Tool" empty="No MCP calls in this time range" events={mcpData?.events ?? []} />
            </Card>
          </CardGrid>
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

interface EventsTableProps {
  nameHeader: string
  empty: string
  events: Array<{
    id: number
    timestamp: string
    name: string
    duration_ms: number | null
    status: string | null
  }>
}

const STATUS_TONES: Record<string, Tone> = { success: 'success', failure: 'danger', error: 'danger', pending: 'warning' }

const EventsTable: React.FC<EventsTableProps> = ({ nameHeader, empty, events }) => {
  if (events.length === 0) return <p className="text-slate-500 text-center py-4">{empty}</p>

  return (
    <Table>
      <thead>
        <tr><th>Time</th><th>{nameHeader}</th><th>Duration</th><th>Status</th></tr>
      </thead>
      <tbody>
        {events.map(event => (
          <tr key={event.id}>
            <td className="text-slate-600">{formatTimestamp(event.timestamp)}</td>
            <td className="text-slate-800 font-medium truncate max-w-48">{event.name}</td>
            <td className="text-slate-600">{event.duration_ms !== null ? `${Math.round(event.duration_ms)}ms` : '-'}</td>
            <td><Badge tone={STATUS_TONES[event.status ?? ''] ?? 'neutral'}>{event.status ?? 'unknown'}</Badge></td>
          </tr>
        ))}
      </tbody>
    </Table>
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

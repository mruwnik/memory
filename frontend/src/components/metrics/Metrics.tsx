import { useState, useEffect, useCallback, useRef } from 'react'
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
import './Metrics.css'

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
    <div className="metrics-page">
      <header className="metrics-header">
        <div className="metrics-header-left">
          <Link to="/ui/dashboard" className="back-link">&larr; Dashboard</Link>
          <h1>System Metrics</h1>
        </div>
        <div className="metrics-header-right">
          <TimeRangeSelector value={timeRange} onChange={setTimeRange} />
          <RefreshIntervalSelector value={refreshInterval} onChange={setRefreshInterval} />
          <button onClick={loadData} className="refresh-btn" disabled={loading}>
            {loading ? 'Loading...' : 'Refresh'}
          </button>
          {lastRefresh && (
            <span className="last-refresh">
              Updated {formatTimestamp(lastRefresh.toISOString())}
            </span>
          )}
        </div>
      </header>

      {error && (
        <div className="metrics-error">
          <p>{error}</p>
          <button onClick={loadData}>Retry</button>
        </div>
      )}

      {loading && !summary && (
        <div className="metrics-loading">
          <p>Loading metrics...</p>
        </div>
      )}

      {summary && (
        <>
          <section className="metrics-summary">
            <SummaryCards
              totalEvents={totalEvents}
              successRate={successRate}
              avgDuration={avgDuration}
              systemMetrics={systemData?.latest ?? {}}
            />
          </section>

          <section className="metrics-charts">
            <div className="chart-container chart-full">
              <h3 className="chart-title">System Health</h3>
              {systemData && <SystemHealthChart data={systemData} />}
            </div>

            <div className="chart-container">
              <h3 className="chart-title">Task Performance</h3>
              {summary && <TaskDurationChart data={summary.metrics.filter(m => m.metric_type === 'task')} />}
            </div>

            <div className="chart-container">
              <h3 className="chart-title">MCP Tool Usage</h3>
              {summary && <McpUsageChart data={summary.metrics.filter(m => m.metric_type === 'mcp_call')} />}
            </div>
          </section>

          <section className="metrics-tables">
            <div className="table-container">
              <h3 className="table-title">Recent Task Executions</h3>
              <TaskTable events={taskData?.events ?? []} />
            </div>

            <div className="table-container">
              <h3 className="table-title">Recent MCP Calls</h3>
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
    <div className="time-range-selector">
      {ranges.map(range => (
        <button
          key={range.value}
          className={`time-range-btn ${value === range.value ? 'active' : ''}`}
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
    <div className="refresh-interval-selector">
      <span className="selector-label">Auto:</span>
      {intervals.map(interval => (
        <button
          key={interval.value}
          className={`time-range-btn ${value === interval.value ? 'active' : ''}`}
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
    return <p className="no-data">No task events in this time range</p>
  }

  return (
    <table className="metrics-table">
      <thead>
        <tr>
          <th>Time</th>
          <th>Task</th>
          <th>Duration</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {events.map(event => (
          <tr key={event.id}>
            <td>{formatTimestamp(event.timestamp)}</td>
            <td className="task-name">{event.name}</td>
            <td>{event.duration_ms !== null ? `${Math.round(event.duration_ms)}ms` : '-'}</td>
            <td>
              <span className={`status-badge status-${event.status}`}>
                {event.status}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
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
    return <p className="no-data">No MCP calls in this time range</p>
  }

  return (
    <table className="metrics-table">
      <thead>
        <tr>
          <th>Time</th>
          <th>Tool</th>
          <th>Duration</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {events.map(event => (
          <tr key={event.id}>
            <td>{formatTimestamp(event.timestamp)}</td>
            <td className="tool-name">{event.name}</td>
            <td>{event.duration_ms !== null ? `${Math.round(event.duration_ms)}ms` : '-'}</td>
            <td>
              <span className={`status-badge status-${event.status}`}>
                {event.status}
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
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

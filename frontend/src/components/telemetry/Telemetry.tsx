import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import {
  useTelemetry,
  TelemetryMetricsResponse,
  TelemetryRawResponse,
  ToolUsageResponse,
} from '@/hooks/useTelemetry'
import { TokenUsageChart } from './TokenUsageChart'
import { CostChart } from './CostChart'
import { SessionActivityChart } from './SessionActivityChart'
import { TelemetrySummaryCards } from './TelemetrySummaryCards'
import { RecentEventsTable } from './RecentEventsTable'
import { ToolBreakdownChart } from './ToolBreakdownChart'
import UserSelector, { useUserSelection, SelectedUser } from '@/components/common/UserSelector'

type TimeRange = '1h' | '6h' | '24h' | '7d'

const timeRangeToParams = (range: TimeRange): { from: Date; granularity: number } => {
  const now = new Date()
  switch (range) {
    case '1h':
      return { from: new Date(now.getTime() - 60 * 60 * 1000), granularity: 5 }
    case '6h':
      return { from: new Date(now.getTime() - 6 * 60 * 60 * 1000), granularity: 15 }
    case '24h':
      return { from: new Date(now.getTime() - 24 * 60 * 60 * 1000), granularity: 60 }
    case '7d':
      return { from: new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000), granularity: 360 }
    default:
      return { from: new Date(now.getTime() - 24 * 60 * 60 * 1000), granularity: 60 }
  }
}

const Telemetry: React.FC = () => {
  const [timeRange, setTimeRange] = useState<TimeRange>('24h')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)
  const [selectedUser, setSelectedUser] = useUserSelection('telemetrySelectedUser')

  // Data state
  const [tokenUsage, setTokenUsage] = useState<TelemetryMetricsResponse | null>(null)
  const [costUsage, setCostUsage] = useState<TelemetryMetricsResponse | null>(null)
  const [sessionActivity, setSessionActivity] = useState<TelemetryMetricsResponse | null>(null)
  const [recentEvents, setRecentEvents] = useState<TelemetryRawResponse | null>(null)
  const [toolUsage, setToolUsage] = useState<ToolUsageResponse | null>(null)

  const { getMetrics, getRawEvents, getToolUsage } = useTelemetry()

  // Convert selectedUser to userId for API calls
  const userId = selectedUser.type === 'user' ? selectedUser.id : undefined

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)

    const { from, granularity } = timeRangeToParams(timeRange)
    const to = new Date()

    try {
      const [tokenRes, costRes, sessionRes, eventsRes, toolRes] = await Promise.all([
        getMetrics('token.usage', { from, to, granularity, groupBy: ['source'], userId }),
        getMetrics('cost.usage', { from, to, granularity, groupBy: ['source'], userId }),
        getMetrics('session.count', { from, to, granularity, groupBy: [], userId }),
        getRawEvents({ from, to, limit: 50, userId }),
        getToolUsage({ from, to, userId }),
      ])

      setTokenUsage(tokenRes)
      setCostUsage(costRes)
      setSessionActivity(sessionRes)
      setRecentEvents(eventsRes)
      setToolUsage(toolRes)
      setLastRefresh(new Date())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load telemetry')
    } finally {
      setLoading(false)
    }
  }, [timeRange, getMetrics, getRawEvents, getToolUsage, userId])

  useEffect(() => {
    loadData()
  }, [loadData])

  // Calculate summary stats
  const totalTokens = tokenUsage?.data.reduce((sum, d) => sum + (d.sum ?? 0), 0) ?? 0
  const totalCost = costUsage?.data.reduce((sum, d) => sum + (d.sum ?? 0), 0) ?? 0
  const totalSessions = sessionActivity?.data.reduce((sum, d) => sum + d.count, 0) ?? 0
  const eventCount = recentEvents?.total ?? 0

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <header className="flex flex-wrap items-center gap-4 mb-6 pb-4 border-b border-slate-200">
        <div className="flex items-center gap-4 flex-1">
          <Link to="/ui/dashboard" className="text-primary hover:underline">&larr; Dashboard</Link>
          <h1 className="text-2xl font-semibold text-slate-800">Claude Code Telemetry</h1>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <UserSelector value={selectedUser} onChange={setSelectedUser} />
          <TimeRangeSelector value={timeRange} onChange={setTimeRange} />
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

      {loading && !tokenUsage && (
        <div className="text-center py-12 text-slate-500">
          <p>Loading telemetry...</p>
        </div>
      )}

      {tokenUsage && (
        <>
          <section className="mb-8">
            <TelemetrySummaryCards
              totalTokens={totalTokens}
              totalCost={totalCost}
              totalSessions={totalSessions}
              eventCount={eventCount}
            />
          </section>

          <section className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
            <div className="bg-white p-6 rounded-xl shadow-md">
              <h3 className="text-lg font-semibold text-slate-800 mb-4">Token Usage</h3>
              <TokenUsageChart data={tokenUsage} />
            </div>

            <div className="bg-white p-6 rounded-xl shadow-md">
              <h3 className="text-lg font-semibold text-slate-800 mb-4">Cost Over Time</h3>
              <CostChart data={costUsage} />
            </div>
          </section>

          <section className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
            <div className="bg-white p-6 rounded-xl shadow-md">
              <h3 className="text-lg font-semibold text-slate-800 mb-4">Session Activity</h3>
              <SessionActivityChart data={sessionActivity} />
            </div>

            <div className="bg-white p-6 rounded-xl shadow-md">
              <h3 className="text-lg font-semibold text-slate-800 mb-4">Recent Events</h3>
              <RecentEventsTable events={recentEvents?.events ?? []} />
            </div>
          </section>

          <section className="mb-8">
            <div className="bg-white p-6 rounded-xl shadow-md">
              <h3 className="text-lg font-semibold text-slate-800 mb-4">Token Usage by Tool</h3>
              <ToolBreakdownChart data={toolUsage} />
            </div>
          </section>
        </>
      )}
    </div>
  )
}

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

export default Telemetry

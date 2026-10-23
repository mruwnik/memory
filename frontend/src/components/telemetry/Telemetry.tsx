import { useState, useEffect, useCallback } from 'react'
import {
  useTelemetry,
  TelemetryMetricsResponse,
  TelemetryRawResponse,
  ToolUsageResponse,
  SessionStatsResponse,
  TelemetryUser,
} from '@/hooks/useTelemetry'
import { TokenUsageChart } from './TokenUsageChart'
import { CostChart } from './CostChart'
import { SessionActivityChart } from './SessionActivityChart'
import { TelemetrySummaryCards } from './TelemetrySummaryCards'
import { RecentEventsTable } from './RecentEventsTable'
import { ToolBreakdownChart } from './ToolBreakdownChart'
import { SessionBreakdownChart } from './SessionBreakdownChart'
import UserSelector, { useUserSelection, SelectedUser } from '@/components/common/UserSelector'
import { Button, Card, Notice, Page, ToggleGroup } from '@/components/shared/ui'

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
  const [sessionStats, setSessionStats] = useState<SessionStatsResponse | null>(null)
  const [usersWithTelemetry, setUsersWithTelemetry] = useState<TelemetryUser[]>([])

  const { getMetrics, getRawEvents, getToolUsage, getSessionStats, getUsersWithTelemetry } = useTelemetry()

  // Load users with telemetry data for the selector filter
  useEffect(() => {
    getUsersWithTelemetry()
      .then(setUsersWithTelemetry)
      .catch(console.error)
  }, [getUsersWithTelemetry])

  // Convert selectedUser to userId for API calls
  // Note: id=0 is a placeholder meaning "user not loaded yet", so treat it as undefined
  const userId = selectedUser.id !== 0 ? selectedUser.id : undefined

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)

    const { from, granularity } = timeRangeToParams(timeRange)
    const to = new Date()

    try {
      const [tokenRes, costRes, sessionRes, eventsRes, toolRes, sessionStatsRes] = await Promise.all([
        getMetrics('token.usage', { from, to, granularity, groupBy: ['source'], userId }),
        getMetrics('cost.usage', { from, to, granularity, groupBy: ['source'], userId }),
        getMetrics('session.count', { from, to, granularity, groupBy: [], userId }),
        getRawEvents({ from, to, limit: 50, userId }),
        getToolUsage({ from, to, userId }),
        getSessionStats({ from, to, userId }),
      ])

      setTokenUsage(tokenRes)
      setCostUsage(costRes)
      setSessionActivity(sessionRes)
      setRecentEvents(eventsRes)
      setToolUsage(toolRes)
      setSessionStats(sessionStatsRes)
      setLastRefresh(new Date())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load telemetry')
    } finally {
      setLoading(false)
    }
  }, [timeRange, getMetrics, getRawEvents, getToolUsage, getSessionStats, userId])

  useEffect(() => {
    loadData()
  }, [loadData])

  // Calculate summary stats
  const totalTokens = tokenUsage?.data.reduce((sum, d) => sum + (d.sum ?? 0), 0) ?? 0
  const totalCost = costUsage?.data.reduce((sum, d) => sum + (d.sum ?? 0), 0) ?? 0
  const totalSessions = sessionActivity?.data.reduce((sum, d) => sum + d.count, 0) ?? 0
  const eventCount = recentEvents?.total ?? 0

  return (
    <Page
      title="Claude Code Telemetry"
      actions={<>
        <div className="flex flex-wrap items-center gap-3">
          <UserSelector value={selectedUser} onChange={setSelectedUser} filterToUsers={usersWithTelemetry} />
          <TimeRangeSelector value={timeRange} onChange={setTimeRange} />
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
            <Card title="Token Usage">
              <TokenUsageChart data={tokenUsage} />
            </Card>

            <Card title="Cost Over Time">
              <CostChart data={costUsage} />
            </Card>
          </section>

          <section className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
            <Card title="Session Activity">
              <SessionActivityChart data={sessionActivity} />
            </Card>

            <Card title="Recent Events">
              <RecentEventsTable events={recentEvents?.events ?? []} />
            </Card>
          </section>

          <section className="mb-8">
            <Card title="Token Usage by Tool">
              <ToolBreakdownChart data={toolUsage} />
            </Card>
          </section>

          <section className="mb-8">
            <Card title="Session Breakdown">
              <p className="text-sm text-slate-500 mb-4">
                Token consumption by individual session. Sessions are categorized by usage volume:
                high (top 20%), medium (next 30%), and low (bottom 50%).
              </p>
              <SessionBreakdownChart data={sessionStats} />
            </Card>
          </section>
        </>
      )}
    </Page>
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

  return <ToggleGroup label="Time range" options={ranges} value={value} onChange={onChange} />
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

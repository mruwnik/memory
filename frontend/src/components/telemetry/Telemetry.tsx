import { useState, useEffect, useCallback, useRef } from 'react'
import {
  useTelemetry,
  TelemetryMetricsResponse,
  TelemetryRawResponse,
  ToolUsageResponse,
  SessionStatsResponse,
  TelemetryApiKey,
  TelemetryUser,
} from '@/hooks/useTelemetry'
import { TokenUsageChart } from './TokenUsageChart'
import { CostChart } from './CostChart'
import { SessionActivityChart } from './SessionActivityChart'
import { TelemetrySummaryCards } from './TelemetrySummaryCards'
import { RecentEventsTable } from './RecentEventsTable'
import { ToolBreakdownChart } from './ToolBreakdownChart'
import { SessionBreakdownChart } from './SessionBreakdownChart'
import { TimeRange } from '@/hooks/useMetrics'
import { TimeRangeSelector } from '@/components/metrics/TimeRangeSelector'
import UserSelector, { useUserSelection, SelectedUser } from '@/components/common/UserSelector'
import { Card, CardGrid, LoadingState, Notice, Page, RefreshButton, Select, Toolbar } from '@/components/shared/ui'

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
  const [apiKeys, setApiKeys] = useState<TelemetryApiKey[]>([])
  const [apiKeyId, setApiKeyId] = useState<number | undefined>(undefined)
  const [keyUsage, setKeyUsage] = useState<TelemetryMetricsResponse | null>(null)

  const { getMetrics, getRawEvents, getToolUsage, getSessionStats, getApiKeysWithTelemetry, getUsersWithTelemetry } = useTelemetry()

  // Load users with telemetry data for the selector filter
  useEffect(() => {
    getUsersWithTelemetry()
      .then(setUsersWithTelemetry)
      .catch(console.error)
  }, [getUsersWithTelemetry])

  // Convert selectedUser to userId for API calls
  // Note: id=0 is a placeholder meaning "user not loaded yet", so treat it as undefined
  const userId = selectedUser.id !== 0 ? selectedUser.id : undefined

  useEffect(() => {
    let cancelled = false
    getApiKeysWithTelemetry(userId)
      .then((keys) => {
        if (cancelled) return
        setApiKeys(keys)
        setApiKeyId((id) => (keys.some((k) => k.id === id) ? id : undefined))
      })
      .catch(console.error)
    return () => {
      cancelled = true
    }
  }, [getApiKeysWithTelemetry, userId])

  const latestLoad = useRef(0)

  // Keys belong to a user, so drop the selection in the same render as the user change.
  const changeUser = (next: typeof selectedUser) => {
    setSelectedUser(next)
    setApiKeyId(undefined)
  }

  const loadData = useCallback(async () => {
    const load = ++latestLoad.current
    const isStale = () => load !== latestLoad.current
    setLoading(true)
    setError(null)

    const { from, granularity } = timeRangeToParams(timeRange)
    const to = new Date()

    try {
      const filters = { from, to, userId, apiKeyId }
      const [tokenRes, costRes, sessionRes, eventsRes, toolRes, sessionStatsRes, keyUsageRes] = await Promise.all([
        getMetrics('token.usage', { ...filters, granularity, groupBy: ['source'] }),
        getMetrics('cost.usage', { ...filters, granularity, groupBy: ['source'] }),
        getMetrics('session.count', { ...filters, granularity, groupBy: [] }),
        getRawEvents({ ...filters, limit: 50 }),
        getToolUsage({ from, to, userId }),
        getSessionStats(filters),
        apiKeyId === undefined
          ? getMetrics('token.usage', { ...filters, granularity, groupBy: ['api_key_id'] })
          : Promise.resolve(null),
      ])
      if (isStale()) return

      setTokenUsage(tokenRes)
      setCostUsage(costRes)
      setSessionActivity(sessionRes)
      setRecentEvents(eventsRes)
      setToolUsage(toolRes)
      setSessionStats(sessionStatsRes)
      setKeyUsage(keyUsageRes)
      setLastRefresh(new Date())
    } catch (err) {
      if (isStale()) return
      setError(err instanceof Error ? err.message : 'Failed to load telemetry')
    } finally {
      if (!isStale()) setLoading(false)
    }
  }, [timeRange, getMetrics, getRawEvents, getToolUsage, getSessionStats, userId, apiKeyId])

  useEffect(() => {
    loadData()
  }, [loadData])

  // Calculate summary stats
  const totalTokens = tokenUsage?.data.reduce((sum, d) => sum + (d.sum ?? 0), 0) ?? 0
  const totalCost = costUsage?.data.reduce((sum, d) => sum + (d.sum ?? 0), 0) ?? 0
  const totalSessions = sessionActivity?.data.reduce((sum, d) => sum + d.count, 0) ?? 0
  const eventCount = recentEvents?.total ?? 0
  const keyNames = new Map(apiKeys.map((k) => [k.id, apiKeyName(k)]))

  return (
    <Page
      title="Telemetry"
      width="wide"
    >
      <Toolbar actions={<>
        {lastRefresh && <span className="text-sm text-slate-600">Updated {formatTimestamp(lastRefresh.toISOString())}</span>}
        <RefreshButton onClick={loadData} loading={loading} label="Refresh telemetry" />
      </>}>
        <TimeRangeSelector value={timeRange} onChange={setTimeRange} />
        <UserSelector value={selectedUser} onChange={changeUser} filterToUsers={usersWithTelemetry} />
        {apiKeys.length > 0 && (
          <Select
            aria-label="API key"
            value={apiKeyId ?? ''}
            onChange={(e) => setApiKeyId(e.target.value ? Number(e.target.value) : undefined)}
          >
            <option value="">All API keys</option>
            {apiKeys.map((k) => (
              <option key={k.id} value={k.id}>{apiKeyName(k)}</option>
            ))}
          </Select>
        )}
      </Toolbar>

      {error && <Notice tone="error" onRetry={loadData}>{error}</Notice>}

      {loading && !tokenUsage && <LoadingState message="Loading telemetry..." />}

      {tokenUsage && (
        <>
          <TelemetrySummaryCards
            totalTokens={totalTokens}
            totalCost={totalCost}
            totalSessions={totalSessions}
            eventCount={eventCount}
          />
          <CardGrid>
            <Card title="Token usage">
              <TokenUsageChart data={tokenUsage} />
            </Card>
            <Card title="Cost over time">
              <CostChart data={costUsage} />
            </Card>
            <Card title="Session activity">
              <SessionActivityChart data={sessionActivity} />
            </Card>
            <Card title="Recent events">
              <RecentEventsTable events={recentEvents?.events ?? []} />
            </Card>
            {apiKeys.length > 0 && apiKeyId === undefined && (
              <Card title="Token usage by API key" className="lg:col-span-2">
                <TokenUsageChart
                  data={keyUsage}
                  seriesOf={(p) => (p.api_key_id == null ? 'No key' : keyNames.get(p.api_key_id) ?? `Key #${p.api_key_id}`)}
                />
              </Card>
            )}
            <Card
              title="Token usage by tool"
              className="lg:col-span-2"
              description={
                apiKeyId === undefined
                  ? undefined
                  : 'Tool usage comes from session transcripts and covers all API keys.'
              }
            >
              <ToolBreakdownChart data={toolUsage} />
            </Card>
            <Card title="Session breakdown" className="lg:col-span-2"
              description="Token consumption by individual session. Sessions are categorized by usage volume: high (top 20%), medium (next 30%), and low (bottom 50%).">
              <SessionBreakdownChart data={sessionStats} />
            </Card>
          </CardGrid>
        </>
      )}
    </Page>
  )
}

const apiKeyName = (key: TelemetryApiKey): string =>
  `${key.name || `${key.key_type} key #${key.id}`}${key.revoked ? ' (revoked)' : ''}`

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

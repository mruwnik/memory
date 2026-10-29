import { useState, useEffect, useCallback, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  useUsage,
  TelemetryMetricsResponse,
  TelemetryRawResponse,
  ToolUsageResponse,
  SessionStatsResponse,
  TelemetryApiKey,
  UsageGroupBy,
  UsageInterval,
  UsageResponse,
} from '@/hooks/useUsage'
import { useAuth } from '@/hooks/useAuth'
import { TokenUsageChart } from './TokenUsageChart'
import { SessionActivityChart } from './SessionActivityChart'
import { UsageSummaryCards } from './UsageSummaryCards'
import { RecentEventsTable } from './RecentEventsTable'
import { ToolBreakdownChart } from './ToolBreakdownChart'
import { SessionBreakdownChart } from './SessionBreakdownChart'
import { GROUP_LABELS, Measure, UsageChart } from './UsageChart'
import { AuditLog } from './AuditLog'
import UserSelector, { useUserSelection } from '@/components/common/UserSelector'
import {
  ButtonLink, Card, CardGrid, EmptyState, LoadingState, Notice, Page, RefreshButton, Section, Select, Tabs, ToggleGroup, Toolbar,
} from '@/components/shared/ui'

type Range = '24h' | '7d' | '30d' | '90d'

const RANGES: { value: Range; label: string; hours: number; intervals: UsageInterval[]; granularity: number }[] = [
  { value: '24h', label: '24 hours', hours: 24, intervals: ['hour'], granularity: 60 },
  { value: '7d', label: '7 days', hours: 24 * 7, intervals: ['hour', 'day'], granularity: 360 },
  { value: '30d', label: '30 days', hours: 24 * 30, intervals: ['day', 'week'], granularity: 1440 },
  { value: '90d', label: '90 days', hours: 24 * 90, intervals: ['day', 'week', 'month'], granularity: 1440 },
]

const INTERVAL_LABELS: Record<UsageInterval, string> = { hour: 'Hourly', day: 'Daily', week: 'Weekly', month: 'Monthly' }
const GROUP_ORDER: UsageGroupBy[] = ['kind', 'model', 'agent', 'key', 'user']
const MEASURES: { value: Measure; label: string }[] = [{ value: 'cost', label: 'Cost' }, { value: 'tokens', label: 'Tokens' }]

type View = 'usage' | 'audit'

const Usage = () => {
  const { hasScope } = useAuth()
  const isAdmin = hasScope('admin') || hasScope('*')
  const [params, setParams] = useSearchParams()
  const view: View = isAdmin && params.get('view') === 'audit' ? 'audit' : 'usage'
  const setView = (next: View) => setParams(next === 'audit' ? { view: 'audit' } : {}, { replace: true })

  return (
    <Page
      title="Usage"
      width="wide"
      description="What LLMs cost: memory's own calls, agent runs and Claude Code."
    >
      {isAdmin ? (
        <Tabs label="Usage views" value={view} onChange={setView}
          options={[{ value: 'usage', label: 'Usage' }, { value: 'audit', label: 'Audit log' }]}>
          {view === 'audit' ? <AuditLog /> : <UsageView isAdmin />}
        </Tabs>
      ) : <UsageView isAdmin={false} />}
    </Page>
  )
}

const UsageView = ({ isAdmin }: { isAdmin: boolean }) => {
  const [range, setRange] = useState<Range>('7d')
  const rangeSpec = RANGES.find(r => r.value === range)!
  const [interval, setInterval] = useState<UsageInterval>('day')
  const [groupBy, setGroupBy] = useState<UsageGroupBy>('kind')
  const [measure, setMeasure] = useState<Measure>('cost')
  const [selectedUser, setSelectedUser] = useUserSelection('usageSelectedUser', true)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)

  const [usage, setUsage] = useState<UsageResponse | null>(null)
  const [usageError, setUsageError] = useState<string | null>(null)
  const [usageLoading, setUsageLoading] = useState(true)

  const [sessionActivity, setSessionActivity] = useState<TelemetryMetricsResponse | null>(null)
  const [recentEvents, setRecentEvents] = useState<TelemetryRawResponse | null>(null)
  const [toolUsage, setToolUsage] = useState<ToolUsageResponse | null>(null)
  const [sessionStats, setSessionStats] = useState<SessionStatsResponse | null>(null)
  const [keyUsage, setKeyUsage] = useState<TelemetryMetricsResponse | null>(null)
  const [telemetryError, setTelemetryError] = useState<string | null>(null)
  const [telemetryLoading, setTelemetryLoading] = useState(true)

  const [apiKeys, setApiKeys] = useState<TelemetryApiKey[]>([])
  const [apiKeyId, setApiKeyId] = useState<number | undefined>(undefined)

  const { getUsage, getMetrics, getRawEvents, getToolUsage, getSessionStats, getApiKeysWithTelemetry } = useUsage()

  // Without a filter a user sees usage on their own keys too, not only usage attributed to them.
  // An admin's id 0 means their own user hasn't loaded yet; "All users" (-1) sends no filter.
  const userId = isAdmin && selectedUser.id > 0 ? selectedUser.id : undefined
  const userReady = !isAdmin || selectedUser.id !== 0

  useEffect(() => {
    if (!userReady) return
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
  }, [getApiKeysWithTelemetry, userId, userReady])

  // Keys belong to a user, so drop the selection in the same render as the user change.
  const changeUser = (next: typeof selectedUser) => {
    setSelectedUser(next)
    setApiKeyId(undefined)
  }

  const changeRange = (next: Range) => {
    setRange(next)
    const allowed = RANGES.find(r => r.value === next)!.intervals
    setInterval(current => (allowed.includes(current) ? current : allowed[0]))
  }

  const latestUsage = useRef(0)
  const loadUsage = useCallback(async () => {
    if (!userReady) return
    const load = ++latestUsage.current
    const isStale = () => load !== latestUsage.current
    setUsageLoading(true)
    setUsageError(null)
    const to = new Date()
    const from = new Date(to.getTime() - rangeSpec.hours * 3600_000)
    try {
      const res = await getUsage({ groupBy: [groupBy], interval, from, to, userId })
      if (isStale()) return
      setUsage(res)
      setLastRefresh(new Date())
    } catch (err) {
      if (isStale()) return
      setUsage(null)
      setUsageError(err instanceof Error ? err.message : 'Failed to load usage')
    } finally {
      if (!isStale()) setUsageLoading(false)
    }
  }, [getUsage, groupBy, interval, rangeSpec.hours, userId, userReady])

  const latestTelemetry = useRef(0)
  const loadTelemetry = useCallback(async () => {
    if (!userReady) return
    const load = ++latestTelemetry.current
    const isStale = () => load !== latestTelemetry.current
    setTelemetryLoading(true)
    setTelemetryError(null)
    const to = new Date()
    const from = new Date(to.getTime() - rangeSpec.hours * 3600_000)
    const granularity = rangeSpec.granularity
    try {
      const filters = { from, to, userId, apiKeyId }
      const [sessionRes, eventsRes, toolRes, sessionStatsRes, keyUsageRes] = await Promise.all([
        getMetrics('session.count', { ...filters, granularity, groupBy: [] }),
        getRawEvents({ ...filters, limit: 50 }),
        getToolUsage({ from, to, userId }),
        getSessionStats(filters),
        apiKeyId === undefined
          ? getMetrics('token.usage', { ...filters, granularity, groupBy: ['api_key_id'] })
          : Promise.resolve(null),
      ])
      if (isStale()) return
      setSessionActivity(sessionRes)
      setRecentEvents(eventsRes)
      setToolUsage(toolRes)
      setSessionStats(sessionStatsRes)
      setKeyUsage(keyUsageRes)
    } catch (err) {
      if (isStale()) return
      setSessionActivity(null)
      setRecentEvents(null)
      setTelemetryError(err instanceof Error ? err.message : 'Failed to load Claude Code telemetry')
    } finally {
      if (!isStale()) setTelemetryLoading(false)
    }
  }, [getMetrics, getRawEvents, getToolUsage, getSessionStats, rangeSpec, userId, apiKeyId, userReady])

  useEffect(() => { loadUsage() }, [loadUsage])
  useEffect(() => { loadTelemetry() }, [loadTelemetry])

  const refresh = () => {
    loadUsage()
    loadTelemetry()
  }

  const totalCost = usage?.data.reduce((sum, d) => sum + d.cost_usd, 0) ?? 0
  const totalTokens = usage?.data.reduce((sum, d) => sum + d.tokens, 0) ?? 0
  const totalSessions = sessionActivity?.data.reduce((sum, d) => sum + d.count, 0) ?? null
  const eventCount = recentEvents?.total ?? null
  const keyNames = new Map(apiKeys.map((k) => [k.id, apiKeyName(k)]))
  const noTelemetry = !eventCount && (sessionStats?.sessions.length ?? 0) === 0

  return (
    <>
      <Toolbar actions={<>
        {lastRefresh && <span className="text-sm text-slate-600">Updated {formatTimestamp(lastRefresh)}</span>}
        <RefreshButton onClick={refresh} loading={usageLoading || telemetryLoading} label="Refresh usage" />
      </>}>
        <ToggleGroup label="Time range" options={RANGES} value={range} onChange={changeRange} />
        <UserSelector value={selectedUser} onChange={changeUser} showAllOption />
      </Toolbar>

      {usageError && <Notice tone="error" onRetry={loadUsage}>{usageError}</Notice>}

      {!usage && !usageError && <LoadingState message="Loading usage..." />}

      {usage && (
        <>
          <UsageSummaryCards totalCost={totalCost} totalTokens={totalTokens} totalSessions={totalSessions} eventCount={eventCount} />
          <Card
            title="Spend over time"
            actions={<>
              <ToggleGroup label="Measure" options={MEASURES} value={measure} onChange={setMeasure} />
              {rangeSpec.intervals.length > 1 && (
                <ToggleGroup
                  label="Interval"
                  options={rangeSpec.intervals.map(i => ({ value: i, label: INTERVAL_LABELS[i] }))}
                  value={interval}
                  onChange={setInterval}
                />
              )}
              <Select aria-label="Group by" className="sm:w-auto" value={groupBy} onChange={e => setGroupBy(e.target.value as UsageGroupBy)}>
                {GROUP_ORDER.map(g => <option key={g} value={g}>By {GROUP_LABELS[g].toLowerCase()}</option>)}
              </Select>
            </>}
          >
            {usage.data.length === 0 ? (
              <EmptyState
                message={<>
                  <p className="font-medium text-slate-700">No usage in this period</p>
                  <p className="mt-1 text-sm">
                    Usage comes from memory's own LLM calls (summaries and search steps, paid with an LLM key),
                    agent runs, and Claude Code sessions that send telemetry to this server.
                  </p>
                </>}
                action={<ButtonLink to="/ui/keys">Manage LLM keys</ButtonLink>}
              />
            ) : <UsageChart data={usage} groupBy={usage.group_by[0] ?? 'kind'} measure={measure} />}
          </Card>
        </>
      )}

      <Section
        title="Claude Code"
        description="Sessions, events and tools from the telemetry Claude Code sends to this server."
        actions={apiKeys.length > 0 && (
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
      >
        {telemetryError && <Notice tone="error" onRetry={loadTelemetry}>{telemetryError}</Notice>}
        {!recentEvents && !telemetryError && <LoadingState message="Loading Claude Code telemetry..." />}
        {recentEvents && noTelemetry && (
          <EmptyState message="No Claude Code telemetry in this period. Claude Code reports here when its OTEL_EXPORTER_OTLP_ENDPOINT points at this server." />
        )}
        {recentEvents && !noTelemetry && (
          <CardGrid>
            <Card title="Session activity">
              <SessionActivityChart data={sessionActivity} />
            </Card>
            <Card title="Recent events">
              <RecentEventsTable events={recentEvents.events} />
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
        )}
      </Section>
    </>
  )
}

const apiKeyName = (key: TelemetryApiKey): string =>
  `${key.name || `${key.key_type} key #${key.id}`}${key.revoked ? ' (revoked)' : ''}`

const formatTimestamp = (date: Date): string => {
  const diffMins = Math.floor((Date.now() - date.getTime()) / 60000)
  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  return date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

export default Usage

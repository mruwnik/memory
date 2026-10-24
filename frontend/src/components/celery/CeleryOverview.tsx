import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  useCelery,
  BeatScheduleEntry,
  TaskActivity,
  TaskActivityEntry,
} from '@/hooks/useCelery'
import { Badge, EmptyState, Notice, Page, RefreshButton, Row, Section, Stat, StatGrid, SubList, Table, Toolbar, type Tone } from '@/components/shared/ui'

const STATUS_TONES: Record<string, Tone> = {
  success: 'success',
  failure: 'danger',
}

function formatRelativeTime(iso: string | null): string {
  if (!iso) return 'Never'
  const diff = Date.now() - new Date(iso).getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return 'Just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

function formatDuration(ms: number | null): string {
  if (ms === null) return '-'
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function shortTaskName(fullName: string): string {
  const parts = fullName.split('.')
  return parts[parts.length - 1].replace(/_/g, ' ')
}

type ActivityRow = TaskActivityEntry & { rate: number | null }

const formatRate = (rate: number | null) => rate === null ? '-' : `${Math.round(rate * 100)}%`

type SortDir = 'asc' | 'desc'

function useSortable<T>(items: T[], defaultKey: keyof T & string, defaultDir: SortDir = 'asc') {
  const [sortKey, setSortKey] = useState<keyof T & string>(defaultKey)
  const [sortDir, setSortDir] = useState<SortDir>(defaultDir)

  const toggle = (key: keyof T & string) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  const sorted = useMemo(() => {
    const copy = [...items]
    copy.sort((a, b) => {
      const av = a[sortKey]
      const bv = b[sortKey]
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      if (av < bv) return sortDir === 'asc' ? -1 : 1
      if (av > bv) return sortDir === 'asc' ? 1 : -1
      return 0
    })
    return copy
  }, [items, sortKey, sortDir])

  return { sorted, sortKey, sortDir, toggle }
}

function SortHeader<T>({
  label,
  column,
  sortKey,
  sortDir,
  toggle,
  className,
}: {
  label: string
  column: keyof T & string
  sortKey: string
  sortDir: SortDir
  toggle: (key: keyof T & string) => void
  className?: string
}) {
  const active = sortKey === column
  return (
    <th className={className} aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : undefined}>
      <button type="button" onClick={() => toggle(column)}
        className="inline-flex items-center gap-1 hover:text-slate-800 focus-visible:outline-2 focus-visible:outline-primary rounded">
        {label}
        <span aria-hidden="true" className={active ? 'text-slate-800' : 'text-slate-500'}>
          {active ? (sortDir === 'asc' ? '\u25B2' : '\u25BC') : '\u2195'}
        </span>
      </button>
    </th>
  )
}

const CeleryOverview = () => {
  const { getBeatSchedule, getTaskActivity } = useCelery()
  const [schedule, setSchedule] = useState<BeatScheduleEntry[]>([])
  const [activity, setActivity] = useState<TaskActivity | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedFailure, setExpandedFailure] = useState<number | null>(null)

  const beatSort = useSortable<BeatScheduleEntry>(schedule, 'name')
  const activityRows = useMemo<ActivityRow[]>(
    () => (activity?.by_task ?? []).map(row => ({ ...row, rate: row.total ? row.success / row.total : null })),
    [activity],
  )
  const activitySort = useSortable<ActivityRow>(activityRows, 'failure', 'desc')

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    Promise.all([getBeatSchedule(), getTaskActivity()])
      .then(([sched, act]) => {
        setSchedule(sched)
        setActivity(act)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [getBeatSchedule, getTaskActivity])

  useEffect(load, [load])

  const totals = activity?.totals

  return (
    <Page title="Celery tasks" width="wide" loading={!activity && loading && 'Loading Celery overview...'} error={activity ? null : error} onRetry={load}>
        <Toolbar actions={<RefreshButton onClick={load} loading={loading} />} />
        {activity && error && <Notice tone="error" onRetry={load}>{error}</Notice>}

        {/* Task Metrics Summary Cards */}
        {totals && (
          <StatGrid>
            <Stat label={`Tasks (${activity?.hours || 24}h)`} value={totals.total} />
            <Stat label="Succeeded" value={totals.success} tone="success" />
            <Stat label="Failed" value={totals.failure} tone={totals.failure ? 'danger' : 'neutral'} />
            <Stat label="Avg duration" value={totals.avg_duration_ms !== null ? formatDuration(totals.avg_duration_ms) : '-'} />
          </StatGrid>
        )}

        {/* Beat Schedule */}
        <Section title={`Beat schedule (${schedule.length} tasks)`}>
          {schedule.length === 0 ? <EmptyState message="No periodic tasks are scheduled" /> : (
          <Table>
              <thead className="bg-slate-50">
                <tr>
                  <SortHeader<BeatScheduleEntry> label="Name" column="name" {...beatSort} />
                  <SortHeader<BeatScheduleEntry> label="Schedule" column="schedule_display" {...beatSort} />
                  <SortHeader<BeatScheduleEntry> label="Last run" column="last_run" {...beatSort} />
                  <SortHeader<BeatScheduleEntry> label="Status" column="last_status" {...beatSort} />
                  <SortHeader<BeatScheduleEntry> label="Duration" column="last_duration_ms" {...beatSort} />
                </tr>
              </thead>
              <tbody>
                {beatSort.sorted.map((entry) => (
                  <tr key={entry.key} className="hover:bg-slate-50">
                    <td>
                      <div className="font-medium text-slate-800">{entry.name}</div>
                      {/* The dotted path crowds the other columns off a phone screen */}
                      <div className="hidden sm:block text-xs text-slate-500 font-mono">{entry.task}</div>
                    </td>
                    <td className="text-slate-600">
                      {entry.schedule_display}
                    </td>
                    <td className="text-slate-600">
                      {formatRelativeTime(entry.last_run)}
                    </td>
                    <td>
                      {entry.last_status ? (
                        <Badge tone={STATUS_TONES[entry.last_status] ?? 'neutral'}>{entry.last_status}</Badge>
                      ) : (
                        <span className="text-slate-500">-</span>
                      )}
                    </td>
                    <td className="text-slate-600">
                      {formatDuration(entry.last_duration_ms)}
                    </td>
                  </tr>
                ))}
              </tbody>
          </Table>
          )}
        </Section>

        {/* Task Activity */}
        {activity && (
          <Section title={`Task activity (last ${activity.hours}h)`}>
            {activity.by_task.length === 0 && activity.recent_failures.length === 0 && (
              <EmptyState message={`No tasks ran in the last ${activity.hours}h`} />
            )}
            {activity.by_task.length > 0 && (
              <Table>
                  <thead className="bg-slate-50">
                    <tr>
                      <SortHeader<ActivityRow> label="Task" column="task" {...activitySort} />
                      <SortHeader<ActivityRow> label="Total" column="total" className="text-right" {...activitySort} />
                      <SortHeader<ActivityRow> label="Success" column="success" className="text-right" {...activitySort} />
                      <SortHeader<ActivityRow> label="Failed" column="failure" className="text-right" {...activitySort} />
                      <SortHeader<ActivityRow> label="Rate" column="rate" className="text-right" {...activitySort} />
                      <SortHeader<ActivityRow> label="Avg duration" column="avg_duration_ms" className="text-right" {...activitySort} />
                    </tr>
                  </thead>
                  <tbody>
                    {activitySort.sorted.map((row) => (
                      <tr key={row.task} className="hover:bg-slate-50">
                        <td>
                          <div className="font-medium text-slate-800 capitalize">
                            {shortTaskName(row.task)}
                          </div>
                          <div className="hidden sm:block text-xs text-slate-500 font-mono">{row.task}</div>
                        </td>
                        <td className="text-right font-medium text-slate-800">
                          {row.total}
                        </td>
                        <td className="text-right text-green-700">
                          {row.success}
                        </td>
                        <td className="text-right text-red-600">
                          {row.failure || '-'}
                        </td>
                        <td className="text-right text-slate-600">
                          {formatRate(row.rate)}
                        </td>
                        <td className="text-right text-slate-600">
                          {formatDuration(row.avg_duration_ms)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
              </Table>
            )}

            {activity.recent_failures.length > 0 && (
              <SubList title="Recent failures">
                {activity.recent_failures.map((failure, idx) => (
                  <Row
                    key={`${failure.task}-${failure.timestamp}`}
                    title={shortTaskName(failure.task)}
                    onSelect={() => setExpandedFailure(expandedFailure === idx ? null : idx)}
                    expanded={expandedFailure === idx}
                    badges={<Badge tone="danger">failure</Badge>}
                    meta={[formatRelativeTime(failure.timestamp), failure.duration_ms !== null && formatDuration(failure.duration_ms)]}
                  >
                    {expandedFailure === idx && failure.error && (
                      <pre className="p-3 bg-red-50 rounded text-xs text-red-800 overflow-x-auto whitespace-pre-wrap">{failure.error}</pre>
                    )}
                    {expandedFailure === idx && failure.labels && Object.keys(failure.labels).length > 0 && (
                      <pre className="p-3 bg-slate-50 rounded text-xs text-slate-600 overflow-x-auto whitespace-pre-wrap">
                        {JSON.stringify(failure.labels, null, 2)}
                      </pre>
                    )}
                  </Row>
                ))}
              </SubList>
            )}
          </Section>
        )}
    </Page>
  )
}

export default CeleryOverview

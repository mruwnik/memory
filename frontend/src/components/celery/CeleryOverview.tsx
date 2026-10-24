import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  useCelery,
  BeatScheduleEntry,
  TaskActivity,
  TaskActivityEntry,
} from '@/hooks/useCelery'
import { Badge, Page, Row, Section, Stat, StatGrid, SubList, Table, type Tone } from '@/components/shared/ui'

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

function successRate(success: number, total: number): string {
  if (total === 0) return '-'
  return `${Math.round((success / total) * 100)}%`
}

type SortDir = 'asc' | 'desc'

function SortIcon({ active, dir }: { active: boolean; dir: SortDir }) {
  return (
    <span className={`ml-1 inline-block ${active ? 'text-slate-800' : 'text-slate-300'}`}>
      {active ? (dir === 'asc' ? '\u25B2' : '\u25BC') : '\u25B4'}
    </span>
  )
}

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
  return (
    <th
      className={`px-4 py-3 text-sm font-medium text-slate-600 cursor-pointer select-none hover:text-slate-800 ${className || ''}`}
      onClick={() => toggle(column)}
    >
      {label}
      <SortIcon active={sortKey === column} dir={sortDir} />
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
  const activitySort = useSortable<TaskActivityEntry>(
    activity?.by_task || [],
    'failure',
    'desc',
  )

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
    <Page title="Celery tasks" width="wide" loading={loading && 'Loading Celery overview...'} error={error} onRetry={load}>
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
          <Table>
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <SortHeader<BeatScheduleEntry> label="Name" column="name" {...beatSort} />
                  <SortHeader<BeatScheduleEntry> label="Schedule" column="schedule_display" {...beatSort} />
                  <SortHeader<BeatScheduleEntry> label="Last run" column="last_run" {...beatSort} />
                  <SortHeader<BeatScheduleEntry> label="Status" column="last_status" {...beatSort} />
                  <SortHeader<BeatScheduleEntry> label="Duration" column="last_duration_ms" {...beatSort} />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {beatSort.sorted.map((entry) => (
                  <tr key={entry.key} className="hover:bg-slate-50">
                    <td className="px-4 py-3 text-sm">
                      <div className="font-medium text-slate-800">{entry.name}</div>
                      <div className="text-xs text-slate-500 font-mono">{entry.task}</div>
                    </td>
                    <td className="px-4 py-3 text-sm text-slate-600">
                      {entry.schedule_display}
                    </td>
                    <td className="px-4 py-3 text-sm text-slate-600">
                      {formatRelativeTime(entry.last_run)}
                    </td>
                    <td className="px-4 py-3 text-sm">
                      {entry.last_status ? (
                        <Badge tone={STATUS_TONES[entry.last_status] ?? 'neutral'}>{entry.last_status}</Badge>
                      ) : (
                        <span className="text-slate-500">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm text-slate-600">
                      {formatDuration(entry.last_duration_ms)}
                    </td>
                  </tr>
                ))}
              </tbody>
          </Table>
        </Section>

        {/* Task Activity */}
        {activity && (
          <Section title={`Task activity (last ${activity.hours}h)`}>
            {activity.by_task.length > 0 && (
              <Table>
                  <thead className="bg-slate-50 border-b border-slate-200">
                    <tr>
                      <SortHeader<TaskActivityEntry> label="Task" column="task" {...activitySort} />
                      <SortHeader<TaskActivityEntry> label="Total" column="total" className="text-right" {...activitySort} />
                      <SortHeader<TaskActivityEntry> label="Success" column="success" className="text-right" {...activitySort} />
                      <SortHeader<TaskActivityEntry> label="Failed" column="failure" className="text-right" {...activitySort} />
                      <SortHeader<TaskActivityEntry> label="Rate" column="success" className="text-right" {...activitySort} />
                      <SortHeader<TaskActivityEntry> label="Avg duration" column="avg_duration_ms" className="text-right" {...activitySort} />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {activitySort.sorted.map((row) => (
                      <tr key={row.task} className="hover:bg-slate-50">
                        <td className="px-4 py-3 text-sm">
                          <div className="font-medium text-slate-800 capitalize">
                            {shortTaskName(row.task)}
                          </div>
                          <div className="text-xs text-slate-500 font-mono">{row.task}</div>
                        </td>
                        <td className="px-4 py-3 text-sm text-right font-medium text-slate-800">
                          {row.total}
                        </td>
                        <td className="px-4 py-3 text-sm text-right text-green-600">
                          {row.success}
                        </td>
                        <td className="px-4 py-3 text-sm text-right text-red-600">
                          {row.failure || '-'}
                        </td>
                        <td className="px-4 py-3 text-sm text-right text-slate-600">
                          {successRate(row.success, row.total)}
                        </td>
                        <td className="px-4 py-3 text-sm text-right text-slate-600">
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

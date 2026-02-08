import { useState, useEffect, useMemo } from 'react'
import { Link } from 'react-router-dom'
import {
  useCelery,
  BeatScheduleEntry,
  TaskActivity,
  TaskActivityEntry,
} from '@/hooks/useCelery'

const STATUS_COLORS: Record<string, string> = {
  success: 'bg-green-100 text-green-700 border-green-200',
  failure: 'bg-red-100 text-red-700 border-red-200',
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

  useEffect(() => {
    Promise.all([getBeatSchedule(), getTaskActivity()])
      .then(([sched, act]) => {
        setSchedule(sched)
        setActivity(act)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [getBeatSchedule, getTaskActivity])

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-gray-500 text-lg">Loading Celery overview...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-red-600 text-lg">Error: {error}</div>
      </div>
    )
  }

  const totals = activity?.totals

  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-white border-b border-slate-200 px-8 py-4 flex items-center gap-4 shadow-sm">
        <Link
          to="/ui/dashboard"
          className="text-slate-500 hover:text-slate-700 no-underline"
        >
          &larr; Dashboard
        </Link>
        <h1 className="text-primary text-2xl font-semibold">Celery Tasks</h1>
      </header>

      <main className="flex-1 p-8 mx-auto w-full max-w-6xl space-y-8">
        {/* Task Metrics Summary Cards */}
        {totals && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-white rounded-xl shadow-md p-6 text-center">
              <div className="text-3xl font-bold text-slate-800">{totals.total}</div>
              <div className="text-sm text-gray-500 mt-1">Tasks ({activity?.hours || 24}h)</div>
            </div>
            <div className="bg-white rounded-xl shadow-md p-6 text-center">
              <div className="text-3xl font-bold text-green-600">{totals.success}</div>
              <div className="text-sm text-gray-500 mt-1">Succeeded</div>
            </div>
            <div className="bg-white rounded-xl shadow-md p-6 text-center">
              <div className="text-3xl font-bold text-red-600">{totals.failure}</div>
              <div className="text-sm text-gray-500 mt-1">Failed</div>
            </div>
            <div className="bg-white rounded-xl shadow-md p-6 text-center">
              <div className="text-3xl font-bold text-slate-800">
                {totals.avg_duration_ms !== null
                  ? formatDuration(totals.avg_duration_ms)
                  : '-'}
              </div>
              <div className="text-sm text-gray-500 mt-1">Avg Duration</div>
            </div>
          </div>
        )}

        {/* Beat Schedule */}
        <section>
          <h2 className="text-slate-800 text-xl font-semibold mb-4">
            Beat Schedule ({schedule.length} tasks)
          </h2>
          <div className="bg-white rounded-xl shadow-md overflow-hidden">
            <table className="w-full text-left">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <SortHeader<BeatScheduleEntry> label="Name" column="name" {...beatSort} />
                  <SortHeader<BeatScheduleEntry> label="Schedule" column="schedule_display" {...beatSort} />
                  <SortHeader<BeatScheduleEntry> label="Last Run" column="last_run" {...beatSort} />
                  <SortHeader<BeatScheduleEntry> label="Status" column="last_status" {...beatSort} />
                  <SortHeader<BeatScheduleEntry> label="Duration" column="last_duration_ms" {...beatSort} />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {beatSort.sorted.map((entry) => (
                  <tr key={entry.key} className="hover:bg-slate-50">
                    <td className="px-4 py-3 text-sm">
                      <div className="font-medium text-slate-800">{entry.name}</div>
                      <div className="text-xs text-slate-400 font-mono">{entry.task}</div>
                    </td>
                    <td className="px-4 py-3 text-sm text-slate-600">
                      {entry.schedule_display}
                    </td>
                    <td className="px-4 py-3 text-sm text-slate-600">
                      {formatRelativeTime(entry.last_run)}
                    </td>
                    <td className="px-4 py-3 text-sm">
                      {entry.last_status ? (
                        <span
                          className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium border ${
                            STATUS_COLORS[entry.last_status] ||
                            'bg-gray-100 text-gray-700 border-gray-200'
                          }`}
                        >
                          {entry.last_status}
                        </span>
                      ) : (
                        <span className="text-slate-400">-</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-sm text-slate-600">
                      {formatDuration(entry.last_duration_ms)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {/* Task Activity */}
        {activity && (
          <section>
            <h2 className="text-slate-800 text-xl font-semibold mb-4">
              Task Activity (last {activity.hours}h)
            </h2>

            {activity.by_task.length > 0 && (
              <div className="bg-white rounded-xl shadow-md overflow-hidden mb-6">
                <table className="w-full text-left">
                  <thead className="bg-slate-50 border-b border-slate-200">
                    <tr>
                      <SortHeader<TaskActivityEntry> label="Task" column="task" {...activitySort} />
                      <SortHeader<TaskActivityEntry> label="Total" column="total" className="text-right" {...activitySort} />
                      <SortHeader<TaskActivityEntry> label="Success" column="success" className="text-right" {...activitySort} />
                      <SortHeader<TaskActivityEntry> label="Failed" column="failure" className="text-right" {...activitySort} />
                      <SortHeader<TaskActivityEntry> label="Rate" column="success" className="text-right" {...activitySort} />
                      <SortHeader<TaskActivityEntry> label="Avg Duration" column="avg_duration_ms" className="text-right" {...activitySort} />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {activitySort.sorted.map((row) => (
                      <tr key={row.task} className="hover:bg-slate-50">
                        <td className="px-4 py-3 text-sm">
                          <div className="font-medium text-slate-800 capitalize">
                            {shortTaskName(row.task)}
                          </div>
                          <div className="text-xs text-slate-400 font-mono">{row.task}</div>
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
                </table>
              </div>
            )}

            {activity.recent_failures.length > 0 && (
              <div>
                <h3 className="text-slate-700 text-lg font-medium mb-3">
                  Recent Failures
                </h3>
                <div className="space-y-2">
                  {activity.recent_failures.map((failure, idx) => (
                    <div
                      key={`${failure.task}-${failure.timestamp}`}
                      className="bg-white rounded-lg shadow-sm border border-red-100 p-4 cursor-pointer"
                      onClick={() =>
                        setExpandedFailure(expandedFailure === idx ? null : idx)
                      }
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700 border border-red-200">
                            {shortTaskName(failure.task)}
                          </span>
                          <span className="text-sm text-slate-500">
                            {formatRelativeTime(failure.timestamp)}
                          </span>
                          {failure.duration_ms !== null && (
                            <span className="text-xs text-slate-400">
                              ({formatDuration(failure.duration_ms)})
                            </span>
                          )}
                        </div>
                        <span className="text-slate-400 text-sm">
                          {expandedFailure === idx ? 'Hide' : 'Show'} details
                        </span>
                      </div>
                      {expandedFailure === idx && (
                        <div className="mt-3 space-y-2">
                          {failure.error && (
                            <pre className="p-3 bg-red-50 rounded text-xs text-red-800 overflow-x-auto whitespace-pre-wrap">
                              {failure.error}
                            </pre>
                          )}
                          {failure.labels && Object.keys(failure.labels).length > 0 && (
                            <pre className="p-3 bg-slate-50 rounded text-xs text-slate-600 overflow-x-auto whitespace-pre-wrap">
                              {JSON.stringify(failure.labels, null, 2)}
                            </pre>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>
        )}
      </main>
    </div>
  )
}

export default CeleryOverview

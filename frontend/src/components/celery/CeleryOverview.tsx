import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import {
  useCelery,
  BeatScheduleEntry,
  IngestionSummary,
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

const CeleryOverview = () => {
  const { getBeatSchedule, getIngestionSummary } = useCelery()
  const [schedule, setSchedule] = useState<BeatScheduleEntry[]>([])
  const [ingestion, setIngestion] = useState<IngestionSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expandedFailure, setExpandedFailure] = useState<number | null>(null)

  useEffect(() => {
    Promise.all([getBeatSchedule(), getIngestionSummary()])
      .then(([sched, ing]) => {
        setSchedule(sched)
        setIngestion(ing)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [getBeatSchedule, getIngestionSummary])

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

  const metrics = ingestion?.task_metrics

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
        {metrics && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-white rounded-xl shadow-md p-6 text-center">
              <div className="text-3xl font-bold text-slate-800">{metrics.total}</div>
              <div className="text-sm text-gray-500 mt-1">Tasks (24h)</div>
            </div>
            <div className="bg-white rounded-xl shadow-md p-6 text-center">
              <div className="text-3xl font-bold text-green-600">{metrics.success}</div>
              <div className="text-sm text-gray-500 mt-1">Succeeded</div>
            </div>
            <div className="bg-white rounded-xl shadow-md p-6 text-center">
              <div className="text-3xl font-bold text-red-600">{metrics.failure}</div>
              <div className="text-sm text-gray-500 mt-1">Failed</div>
            </div>
            <div className="bg-white rounded-xl shadow-md p-6 text-center">
              <div className="text-3xl font-bold text-slate-800">
                {metrics.avg_duration_ms !== null
                  ? formatDuration(metrics.avg_duration_ms)
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
                  <th className="px-4 py-3 text-sm font-medium text-slate-600">Name</th>
                  <th className="px-4 py-3 text-sm font-medium text-slate-600">Schedule</th>
                  <th className="px-4 py-3 text-sm font-medium text-slate-600">Last Run</th>
                  <th className="px-4 py-3 text-sm font-medium text-slate-600">Status</th>
                  <th className="px-4 py-3 text-sm font-medium text-slate-600">Duration</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {schedule.map((entry) => (
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

        {/* Ingestion Summary */}
        {ingestion && (
          <section>
            <h2 className="text-slate-800 text-xl font-semibold mb-4">
              Ingestion Jobs
            </h2>

            {ingestion.by_type.length > 0 && (
              <div className="bg-white rounded-xl shadow-md overflow-hidden mb-6">
                <table className="w-full text-left">
                  <thead className="bg-slate-50 border-b border-slate-200">
                    <tr>
                      <th className="px-4 py-3 text-sm font-medium text-slate-600">Job Type</th>
                      <th className="px-4 py-3 text-sm font-medium text-slate-600 text-right">Pending</th>
                      <th className="px-4 py-3 text-sm font-medium text-slate-600 text-right">Processing</th>
                      <th className="px-4 py-3 text-sm font-medium text-slate-600 text-right">Complete</th>
                      <th className="px-4 py-3 text-sm font-medium text-slate-600 text-right">Failed</th>
                      <th className="px-4 py-3 text-sm font-medium text-slate-600 text-right">Total</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {ingestion.by_type.map((row) => (
                      <tr key={row.job_type} className="hover:bg-slate-50">
                        <td className="px-4 py-3 text-sm font-medium text-slate-800">
                          {row.job_type}
                        </td>
                        <td className="px-4 py-3 text-sm text-right text-yellow-600">
                          {row.pending || '-'}
                        </td>
                        <td className="px-4 py-3 text-sm text-right text-blue-600">
                          {row.processing || '-'}
                        </td>
                        <td className="px-4 py-3 text-sm text-right text-green-600">
                          {row.complete || '-'}
                        </td>
                        <td className="px-4 py-3 text-sm text-right text-red-600">
                          {row.failed || '-'}
                        </td>
                        <td className="px-4 py-3 text-sm text-right font-medium text-slate-800">
                          {row.total}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {ingestion.recent_failures.length > 0 && (
              <div>
                <h3 className="text-slate-700 text-lg font-medium mb-3">
                  Recent Failures
                </h3>
                <div className="space-y-2">
                  {ingestion.recent_failures.map((failure) => (
                    <div
                      key={failure.id}
                      className="bg-white rounded-lg shadow-sm border border-red-100 p-4 cursor-pointer"
                      onClick={() =>
                        setExpandedFailure(
                          expandedFailure === failure.id ? null : failure.id
                        )
                      }
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700 border border-red-200">
                            {failure.job_type}
                          </span>
                          <span className="text-sm text-slate-500">
                            {formatRelativeTime(failure.updated_at)}
                          </span>
                        </div>
                        <span className="text-slate-400 text-sm">
                          {expandedFailure === failure.id ? 'Hide' : 'Show'} details
                        </span>
                      </div>
                      {expandedFailure === failure.id && failure.error_message && (
                        <pre className="mt-3 p-3 bg-red-50 rounded text-xs text-red-800 overflow-x-auto whitespace-pre-wrap">
                          {failure.error_message}
                        </pre>
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

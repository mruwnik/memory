import { useState, useEffect, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import { useJobs, Job, JobStatus } from '@/hooks/useJobs'

type StatusFilter = 'all' | JobStatus

const STATUS_ORDER: Record<string, number> = {
  failed: 0,
  processing: 1,
  pending: 2,
  complete: 3,
}

const FILTERS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'failed', label: 'Failed' },
  { value: 'processing', label: 'Processing' },
  { value: 'pending', label: 'Pending' },
  { value: 'complete', label: 'Complete' },
]

const STATUS_COLORS: Record<string, string> = {
  failed: 'bg-red-100 text-red-700 border-red-200',
  processing: 'bg-blue-100 text-blue-700 border-blue-200',
  pending: 'bg-yellow-100 text-yellow-700 border-yellow-200',
  complete: 'bg-green-100 text-green-700 border-green-200',
}

const Jobs = () => {
  const { listJobs, retryJob } = useJobs()
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [retryingId, setRetryingId] = useState<number | null>(null)
  const [retryError, setRetryError] = useState<string | null>(null)
  const abortControllerRef = useRef<AbortController | null>(null)

  const loadJobs = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    setRetryError(null)
    try {
      const filters = statusFilter !== 'all' ? { status: statusFilter } : {}
      const data = await listJobs({ ...filters, limit: 100 })
      // Check if request was aborted
      if (signal?.aborted) return
      // Sort: failed first, then processing, pending, complete
      const sorted = [...data].sort((a, b) => {
        const aOrder = STATUS_ORDER[a.status] ?? 4
        const bOrder = STATUS_ORDER[b.status] ?? 4
        if (aOrder !== bOrder) return aOrder - bOrder
        // Then by created_at descending (newest first)
        return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      })
      setJobs(sorted)
      setError(null)
    } catch (e) {
      if (signal?.aborted) return
      setError(e instanceof Error ? e.message : 'Failed to load jobs')
    } finally {
      if (!signal?.aborted) {
        setLoading(false)
      }
    }
  }, [listJobs, statusFilter])

  useEffect(() => {
    // Cancel any in-flight request
    abortControllerRef.current?.abort()
    const controller = new AbortController()
    abortControllerRef.current = controller
    loadJobs(controller.signal)
    return () => controller.abort()
  }, [loadJobs])

  const handleRetry = async (jobId: number) => {
    setRetryingId(jobId)
    setRetryError(null)
    try {
      await retryJob(jobId)
      loadJobs()
    } catch (e) {
      setRetryError(e instanceof Error ? e.message : 'Failed to retry job')
    } finally {
      setRetryingId(null)
    }
  }

  const formatRelativeTime = (dateStr: string) => {
    const date = new Date(dateStr)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()
    const diffMins = Math.floor(diffMs / 60000)
    const diffHours = Math.floor(diffMs / 3600000)
    const diffDays = Math.floor(diffMs / 86400000)

    if (diffMins < 1) return 'just now'
    if (diffMins < 60) return `${diffMins}m ago`
    if (diffHours < 24) return `${diffHours}h ago`
    if (diffDays < 7) return `${diffDays}d ago`
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  }

  const formatJobType = (type: string) => {
    return type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
  }

  // Stats are only meaningful when showing all jobs
  const showStats = statusFilter === 'all'
  const pendingCount = jobs.filter(j => j.status === 'pending').length
  const processingCount = jobs.filter(j => j.status === 'processing').length
  const failedCount = jobs.filter(j => j.status === 'failed').length
  const completeCount = jobs.filter(j => j.status === 'complete').length

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <header className="flex items-center gap-4 mb-6 pb-4 border-b border-slate-200">
        <Link to="/ui/dashboard" className="bg-slate-50 text-slate-600 border border-slate-200 py-2 px-4 rounded-md text-sm hover:bg-slate-100">
          Back
        </Link>
        <h1 className="text-2xl font-semibold text-slate-800 flex-1">Jobs</h1>
        <div className="flex gap-3 text-sm">
          {showStats ? (
            <>
              {failedCount > 0 && <span className="text-red-600 font-medium">{failedCount} failed</span>}
              {processingCount > 0 && <span className="text-blue-600 font-medium">{processingCount} processing</span>}
              <span className="text-slate-600">{pendingCount} pending</span>
              <span className="text-slate-600">{completeCount} complete</span>
            </>
          ) : (
            <span className="text-slate-600">Showing {jobs.length} {statusFilter}</span>
          )}
        </div>
      </header>

      <div className="space-y-4">
        {/* Filters */}
        <div className="flex gap-2 items-center">
          {FILTERS.map(f => (
            <button
              key={f.value}
              className={`py-2 px-4 rounded-lg text-sm font-medium transition-colors ${
                statusFilter === f.value
                  ? 'bg-primary text-white'
                  : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-50'
              }`}
              onClick={() => setStatusFilter(f.value)}
            >
              {f.label}
            </button>
          ))}
          <button
            onClick={() => loadJobs()}
            className="w-9 h-9 bg-white border border-slate-200 rounded-lg hover:bg-slate-50 text-lg"
            title="Refresh"
            aria-label="Refresh jobs list"
          >
            &#8635;
          </button>
        </div>

        {/* Retry Error */}
        {retryError && (
          <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg flex justify-between items-center">
            <p>{retryError}</p>
            <button onClick={() => setRetryError(null)} className="text-red-700 hover:underline">Dismiss</button>
          </div>
        )}

        {/* Load Error */}
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg flex justify-between items-center">
            <p>{error}</p>
            <button onClick={() => loadJobs()} className="text-primary hover:underline">Retry</button>
          </div>
        )}

        {/* Loading */}
        {loading && <div className="text-center py-8 text-slate-500">Loading jobs...</div>}

        {/* Empty State */}
        {!loading && jobs.length === 0 && (
          <div className="text-center py-12 text-slate-500 bg-white rounded-xl">
            {statusFilter === 'all'
              ? 'No jobs found'
              : `No ${statusFilter} jobs`}
          </div>
        )}

        {/* Job List */}
        {!loading && jobs.length > 0 && (
          <ul className="space-y-3">
            {jobs.map(job => (
              <li
                key={job.id}
                className={`bg-white p-4 rounded-lg shadow-sm border-l-4 ${
                  STATUS_COLORS[job.status]?.split(' ')[2] ?? 'border-slate-200'
                }`}
              >
                <div className="flex items-start gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-semibold text-slate-800">{formatJobType(job.job_type)}</span>
                      <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[job.status] ?? 'bg-slate-100 text-slate-600'}`}>
                        {job.status}
                      </span>
                    </div>
                    <div className="text-sm text-slate-600 truncate">
                      {job.external_id || `Job #${job.id}`}
                    </div>
                    <div className="flex gap-4 mt-2 text-xs text-slate-500">
                      <span>{formatRelativeTime(job.created_at)}</span>
                      <span>{job.attempts} attempt{job.attempts !== 1 ? 's' : ''}</span>
                      {job.result_type && job.result_id && (
                        <span className="text-primary">{job.result_type} #{job.result_id}</span>
                      )}
                    </div>
                    {job.error_message && (
                      <div className="mt-2 text-sm text-red-600 bg-red-50 p-2 rounded">{job.error_message}</div>
                    )}
                  </div>

                  <div className="shrink-0">
                    {job.status === 'failed' && (
                      <button
                        onClick={() => handleRetry(job.id)}
                        disabled={retryingId === job.id}
                        className="bg-primary text-white py-1.5 px-3 rounded text-sm hover:bg-primary-dark disabled:bg-slate-400"
                        title="Retry job"
                      >
                        {retryingId === job.id ? 'Retrying...' : 'Retry'}
                      </button>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export default Jobs

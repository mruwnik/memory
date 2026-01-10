import { useState, useEffect, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import { useJobs, Job, JobStatus } from '@/hooks/useJobs'
import './Jobs.css'

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
    <div className="jobs-page">
      <header className="jobs-header">
        <Link to="/ui/dashboard" className="back-btn">Back</Link>
        <h1>Jobs</h1>
        <div className="jobs-stats">
          {showStats ? (
            <>
              {failedCount > 0 && <span className="stat stat-failed">{failedCount} failed</span>}
              {processingCount > 0 && <span className="stat stat-processing">{processingCount} processing</span>}
              <span className="stat">{pendingCount} pending</span>
              <span className="stat">{completeCount} complete</span>
            </>
          ) : (
            <span className="stat">Showing {jobs.length} {statusFilter}</span>
          )}
        </div>
      </header>

      <div className="jobs-content">
        {/* Filters */}
        <div className="jobs-filters">
          {FILTERS.map(f => (
            <button
              key={f.value}
              className={`filter-btn ${statusFilter === f.value ? 'active' : ''}`}
              onClick={() => setStatusFilter(f.value)}
            >
              {f.label}
            </button>
          ))}
          <button
            onClick={() => loadJobs()}
            className="refresh-btn"
            title="Refresh"
            aria-label="Refresh jobs list"
          >
            &#8635;
          </button>
        </div>

        {/* Retry Error */}
        {retryError && (
          <div className="jobs-error">
            <p>{retryError}</p>
            <button onClick={() => setRetryError(null)}>Dismiss</button>
          </div>
        )}

        {/* Load Error */}
        {error && (
          <div className="jobs-error">
            <p>{error}</p>
            <button onClick={() => loadJobs()}>Retry</button>
          </div>
        )}

        {/* Loading */}
        {loading && <div className="jobs-loading">Loading jobs...</div>}

        {/* Empty State */}
        {!loading && jobs.length === 0 && (
          <div className="jobs-empty">
            {statusFilter === 'all'
              ? 'No jobs found'
              : `No ${statusFilter} jobs`}
          </div>
        )}

        {/* Job List */}
        {!loading && jobs.length > 0 && (
          <ul className="jobs-list">
            {jobs.map(job => (
              <li key={job.id} className={`job-item status-${job.status}`}>
                <div className="job-content">
                  <div className="job-header">
                    <span className="job-type">{formatJobType(job.job_type)}</span>
                    <span className={`job-status status-${job.status}`}>
                      {job.status}
                    </span>
                  </div>
                  <div className="job-subtitle">
                    {job.external_id || `Job #${job.id}`}
                  </div>
                  <div className="job-meta">
                    <span className="job-time">{formatRelativeTime(job.created_at)}</span>
                    <span className="job-attempts">{job.attempts} attempt{job.attempts !== 1 ? 's' : ''}</span>
                    {job.result_type && job.result_id && (
                      <span className="job-result">
                        {job.result_type} #{job.result_id}
                      </span>
                    )}
                  </div>
                  {job.error_message && (
                    <div className="job-error">{job.error_message}</div>
                  )}
                </div>

                <div className="job-actions">
                  {job.status === 'failed' && (
                    <button
                      onClick={() => handleRetry(job.id)}
                      disabled={retryingId === job.id}
                      className="retry-btn"
                      title="Retry job"
                    >
                      {retryingId === job.id ? 'Retrying...' : 'Retry'}
                    </button>
                  )}
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

import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useJobs, Job, JobStatus, JobSource, JobUser } from '@/hooks/useJobs'
import UserSelector, { useUserSelection, ALL_USERS_ID, NO_USER_ID } from '@/components/common/UserSelector'
import { Button, EmptyState, Input, LoadingState, Notice, Page, Select, ToggleGroup } from '@/components/shared/ui'

type StatusFilter = 'all' | JobStatus

const PAGE_SIZE = 100

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
  const { listJobs, retryJob, reingestJob, getUsersWithJobs, getJobTypes } = useJobs()
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [jobTypeFilter, setJobTypeFilter] = useState<string>('all')
  const [sourceFilter, setSourceFilter] = useState<'all' | JobSource>('all')
  const [createdAfter, setCreatedAfter] = useState<string>('')
  const [createdBefore, setCreatedBefore] = useState<string>('')
  const [page, setPage] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [retryingId, setRetryingId] = useState<number | null>(null)
  const [reingestingId, setReingestingId] = useState<number | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const abortControllerRef = useRef<AbortController | null>(null)
  const [selectedUser, setSelectedUser] = useUserSelection('jobsSelectedUser', true)
  const [usersWithJobs, setUsersWithJobs] = useState<JobUser[]>([])
  const [jobTypes, setJobTypes] = useState<string[]>([])

  // Load users with jobs and job types for filter dropdowns
  useEffect(() => {
    getUsersWithJobs().then(setUsersWithJobs).catch(console.error)
    getJobTypes().then(setJobTypes).catch(console.error)
  }, [getUsersWithJobs, getJobTypes])

  // Convert selectedUser to userId for API calls
  // id=0 is a placeholder meaning "user not loaded yet"
  // ALL_USERS_ID (-1) means show all users' jobs
  // NO_USER_ID (-2) means show system jobs (user_id IS NULL)
  const userId = selectedUser.id === ALL_USERS_ID || selectedUser.id === NO_USER_ID
    ? undefined
    : selectedUser.id !== 0
      ? selectedUser.id
      : undefined

  const showingAllUsers = selectedUser.id === ALL_USERS_ID || selectedUser.id === NO_USER_ID

  // When "System (no user)" is selected, force source=automatic
  const effectiveSourceFilter = selectedUser.id === NO_USER_ID ? 'automatic' : sourceFilter

  // Build a user lookup map for displaying user names on job rows
  const userNameMap = useMemo(() => {
    const map = new Map<number, string>()
    for (const u of usersWithJobs) {
      map.set(u.id, u.name || u.email)
    }
    return map
  }, [usersWithJobs])

  const loadJobs = useCallback(async (signal?: AbortSignal) => {
    setLoading(true)
    setActionError(null)
    try {
      const filters: Record<string, string | number | undefined> = {}
      if (statusFilter !== 'all') filters.status = statusFilter
      if (jobTypeFilter !== 'all') filters.job_type = jobTypeFilter
      if (effectiveSourceFilter !== 'all') filters.source = effectiveSourceFilter
      if (createdAfter) filters.created_after = new Date(createdAfter).toISOString()
      if (createdBefore) filters.created_before = new Date(createdBefore + 'T23:59:59').toISOString()
      const data = await listJobs({ ...filters, limit: PAGE_SIZE, offset: page * PAGE_SIZE, userId } as Parameters<typeof listJobs>[0])
      // Check if request was aborted
      if (signal?.aborted) return
      setHasMore(data.length === PAGE_SIZE)
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
  }, [listJobs, statusFilter, jobTypeFilter, effectiveSourceFilter, createdAfter, createdBefore, page, userId])

  useEffect(() => {
    // Cancel any in-flight request
    abortControllerRef.current?.abort()
    const controller = new AbortController()
    abortControllerRef.current = controller
    loadJobs(controller.signal)
    return () => controller.abort()
  }, [loadJobs])

  // Reset to first page when filters change
  useEffect(() => {
    setPage(0)
  }, [statusFilter, jobTypeFilter, effectiveSourceFilter, createdAfter, createdBefore, userId])

  const handleRetry = async (jobId: number) => {
    setRetryingId(jobId)
    setActionError(null)
    try {
      await retryJob(jobId)
      loadJobs()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Failed to retry job')
    } finally {
      setRetryingId(null)
    }
  }

  const handleReingest = async (jobId: number) => {
    setReingestingId(jobId)
    setActionError(null)
    try {
      await reingestJob(jobId)
      loadJobs()
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Failed to reingest job')
    } finally {
      setReingestingId(null)
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
    <Page
      title="Jobs"
      actions={<>
        <UserSelector value={selectedUser} onChange={setSelectedUser} filterToUsers={usersWithJobs} showAllOption showNoneOption />
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
      </>}
      width="wide"
    >
      <div className="space-y-4">
        {/* Filters */}
        <div className="flex flex-wrap gap-2 items-center">
          <ToggleGroup label="Filter" options={FILTERS} value={statusFilter} onChange={setStatusFilter} />
          {jobTypes.length > 1 && (
            <Select className="w-auto" aria-label="Job type"
              value={jobTypeFilter}
              onChange={(e) => setJobTypeFilter(e.target.value)}
            >
              <option value="all">All types</option>
              {jobTypes.map(t => (
                <option key={t} value={t}>{formatJobType(t)}</option>
              ))}
            </Select>
          )}
          <Select className="w-auto" aria-label="Source"
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value as 'all' | JobSource)}
          >
            <option value="all">All sources</option>
            <option value="manual">Manual</option>
            <option value="automatic">Automatic</option>
          </Select>
          <div className="flex items-center gap-1">
            <Input
              type="date"
              className="w-auto"
              value={createdAfter}
              onChange={(e) => setCreatedAfter(e.target.value)}
              aria-label="From date"
            />
            <span className="text-slate-500 text-xs">to</span>
            <Input
              type="date"
              className="w-auto"
              value={createdBefore}
              onChange={(e) => setCreatedBefore(e.target.value)}
              aria-label="To date"
            />
            {(createdAfter || createdBefore) && (
              <button
                onClick={() => { setCreatedAfter(''); setCreatedBefore('') }}
                className="text-xs text-slate-500 hover:text-slate-700 px-1"
                aria-label="Clear dates"
              >
                &times;
              </button>
            )}
          </div>
          <Button size="icon"
            onClick={() => loadJobs()}
            
            title="Refresh"
            aria-label="Refresh jobs list"
          >
            &#8635;
          </Button>
        </div>

        {/* Action Error */}
        {actionError && (
          <Notice tone="error" onDismiss={() => setActionError(null)}>{actionError}</Notice>
        )}

        {/* Load Error */}
        {error && (
          <Notice tone="error" onRetry={() => loadJobs()}>{error}</Notice>
        )}

        {/* Loading */}
        {loading && <LoadingState message="Loading jobs..." />}

        {/* Empty State */}
        {!loading && jobs.length === 0 && (
          <EmptyState message={statusFilter === 'all'
              ? 'No jobs found'
              : `No ${statusFilter} jobs`} />
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
                      {showingAllUsers && job.user_id && (
                        <span className="text-slate-600 font-medium">
                          {userNameMap.get(job.user_id) || `user #${job.user_id}`}
                        </span>
                      )}
                      {job.result_type && job.result_id && (
                        <span className="text-primary">{job.result_type} #{job.result_id}</span>
                      )}
                    </div>
                    {job.error_message && (
                      <div className="mt-2 text-sm text-red-600 bg-red-50 p-2 rounded">{job.error_message}</div>
                    )}
                  </div>

                  <div className="shrink-0 flex gap-2">
                    {job.status === 'failed' && (
                      <Button variant="primary" size="sm"
                        onClick={() => handleRetry(job.id)}
                        disabled={retryingId === job.id || reingestingId === job.id}
                        title="Retry failed job"
                      >
                        {retryingId === job.id ? 'Retrying...' : 'Retry'}
                      </Button>
                    )}
                    {(job.status === 'complete' || job.status === 'failed') && (
                      <Button size="sm"
                        onClick={() => handleReingest(job.id)}
                        disabled={retryingId === job.id || reingestingId === job.id}
                        title="Re-run this job"
                      >
                        {reingestingId === job.id ? 'Reingesting...' : 'Reingest'}
                      </Button>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}

        {/* Pagination */}
        {!loading && (page > 0 || hasMore) && (
          <div className="flex items-center justify-between pt-2">
            <Button
              onClick={() => setPage(p => Math.max(0, p - 1))}
              disabled={page === 0}
            >
              Previous
            </Button>
            <span className="text-sm text-slate-500">
              Page {page + 1}{hasMore ? '' : ' (last)'}
            </span>
            <Button
              onClick={() => setPage(p => p + 1)}
              disabled={!hasMore}
            >
              Next
            </Button>
          </div>
        )}
      </div>
    </Page>
  )
}

export default Jobs

import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useJobs, Job, JobCounts, JobStatus, JobSource, JobUser } from '@/hooks/useJobs'
import UserSelector, { useUserSelection, ALL_USERS_ID, NO_USER_ID } from '@/components/common/UserSelector'
import { Badge, Button, EmptyState, Input, List, LoadingState, Notice, Page, Pager, RefreshButton, Row, RowError, Select, ToggleGroup, Toolbar, type Tone } from '@/components/shared/ui'
import { formatUserDate } from '@/utils/timezones'

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

const STATUS_TONES: Record<string, Tone> = {
  failed: 'danger',
  processing: 'info',
  pending: 'warning',
  complete: 'success',
}

const Jobs = () => {
  const { listJobs, getJobCounts, retryJob, reingestJob, getUsersWithJobs, getJobTypes } = useJobs()
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [counts, setCounts] = useState<JobCounts | null>(null)
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
    setError(null)
    setActionError(null)
    try {
      const filters: Record<string, string | number | undefined> = {}
      if (statusFilter !== 'all') filters.status = statusFilter
      if (jobTypeFilter !== 'all') filters.job_type = jobTypeFilter
      if (effectiveSourceFilter !== 'all') filters.source = effectiveSourceFilter
      if (createdAfter) filters.created_after = new Date(createdAfter).toISOString()
      if (createdBefore) filters.created_before = new Date(createdBefore + 'T23:59:59').toISOString()
      const { status: _status, ...countFilters } = filters
      const [data, jobCounts] = await Promise.all([
        listJobs({ ...filters, limit: PAGE_SIZE, offset: page * PAGE_SIZE, userId } as Parameters<typeof listJobs>[0]),
        getJobCounts({ ...countFilters, userId } as Parameters<typeof getJobCounts>[0]),
      ])
      // Check if request was aborted
      if (signal?.aborted) return
      setCounts(jobCounts)
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
    } catch (e) {
      if (signal?.aborted) return
      setError(e instanceof Error ? e.message : 'Failed to load jobs')
    } finally {
      if (!signal?.aborted) {
        setLoading(false)
      }
    }
  }, [listJobs, getJobCounts, statusFilter, jobTypeFilter, effectiveSourceFilter, createdAfter, createdBefore, page, userId])

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
    return formatUserDate(date, { month: 'short', day: 'numeric' })
  }

  const formatJobType = (type: string) => {
    return type.replace(/_/g, ' ').replace(/^\w/, c => c.toUpperCase())
  }

  const shown = jobs ?? []
  const summary = counts && [
    counts.failed > 0 && `${counts.failed} failed`,
    counts.processing > 0 && `${counts.processing} processing`,
    `${counts.pending} pending`,
    `${counts.complete} complete`,
  ]

  return (
    <Page
      title="Jobs"
      description={<span className="flex flex-wrap gap-x-3">{(summary || []).filter(Boolean).map(c => <span key={c as string}>{c}</span>)}</span>}
      width="wide"
      loading={jobs === null && 'Loading jobs...'}
      error={jobs === null ? error : null}
      onRetry={() => loadJobs()}
    >
      <Toolbar actions={<RefreshButton onClick={() => loadJobs()} loading={loading} label="Refresh jobs list" />}>
        <ToggleGroup label="Status" options={FILTERS} value={statusFilter} onChange={setStatusFilter} />
        <UserSelector value={selectedUser} onChange={setSelectedUser} filterToUsers={usersWithJobs} showAllOption showNoneOption />
        {jobTypes.length > 1 && (
          <Select className="w-auto" aria-label="Job type" value={jobTypeFilter} onChange={(e) => setJobTypeFilter(e.target.value)}>
            <option value="all">All types</option>
            {jobTypes.map(t => <option key={t} value={t}>{formatJobType(t)}</option>)}
          </Select>
        )}
        <Select className="w-auto" aria-label="Source" value={sourceFilter} onChange={(e) => setSourceFilter(e.target.value as 'all' | JobSource)}>
          <option value="all">All sources</option>
          <option value="manual">Manual</option>
          <option value="automatic">Automatic</option>
        </Select>
        <div className="flex items-center gap-1">
          <Input type="date" className="w-auto" value={createdAfter} onChange={(e) => setCreatedAfter(e.target.value)} aria-label="From date" />
          <span className="text-slate-500 text-xs">to</span>
          <Input type="date" className="w-auto" value={createdBefore} onChange={(e) => setCreatedBefore(e.target.value)} aria-label="To date" />
          {(createdAfter || createdBefore) && (
            <Button variant="ghost" size="sm" onClick={() => { setCreatedAfter(''); setCreatedBefore('') }} aria-label="Clear dates">&times;</Button>
          )}
        </div>
      </Toolbar>

      {actionError && <Notice tone="error" onDismiss={() => setActionError(null)}>{actionError}</Notice>}
      {jobs && error && <Notice tone="error" onRetry={() => loadJobs()}>{error}</Notice>}

      {loading ? <LoadingState message="Loading jobs..." />
        : shown.length === 0 ? <EmptyState message={statusFilter === 'all' ? 'No jobs found' : `No ${statusFilter} jobs`} />
        : (
          <List>
            {shown.map(job => {
              const busy = retryingId === job.id || reingestingId === job.id
              return (
                <Row
                  key={job.id}
                  title={formatJobType(job.job_type)}
                  badges={<Badge tone={STATUS_TONES[job.status] ?? 'neutral'}>{job.status}</Badge>}
                  meta={[
                    job.external_id || `Job #${job.id}`,
                    formatRelativeTime(job.created_at),
                    `${job.attempts} attempt${job.attempts !== 1 ? 's' : ''}`,
                    showingAllUsers && job.user_id && (userNameMap.get(job.user_id) || `user #${job.user_id}`),
                    job.result_type && job.result_id && `${job.result_type} #${job.result_id}`,
                  ]}
                  actions={<>
                    {job.status === 'failed' && (
                      <Button size="sm" onClick={() => handleRetry(job.id)} disabled={busy} title="Retry failed job">
                        {retryingId === job.id ? 'Retrying...' : 'Retry'}
                      </Button>
                    )}
                    {(job.status === 'complete' || job.status === 'failed') && (
                      <Button size="sm" onClick={() => handleReingest(job.id)} disabled={busy} title="Re-run this job">
                        {reingestingId === job.id ? 'Reingesting...' : 'Reingest'}
                      </Button>
                    )}
                  </>}
                >
                  {job.error_message && <RowError>{job.error_message}</RowError>}
                </Row>
              )
            })}
          </List>
        )}

      {!loading && (page > 0 || hasMore) && (
        <Pager page={page} hasMore={hasMore} onPage={setPage} />
      )}
    </Page>
  )
}

export default Jobs

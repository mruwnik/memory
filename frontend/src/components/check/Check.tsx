import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useCheck, PAGE_LIMIT, CheckJob, CheckMode, CheckStatus, AskBody } from '@/hooks/useCheck'
import { formatRelativeTime } from '@/components/sources/shared'

const STATUS_COLORS: Record<CheckStatus, string> = {
  queued: 'bg-yellow-100 text-yellow-700',
  in_flight: 'bg-blue-100 text-blue-700',
  ok: 'bg-green-100 text-green-700',
  error: 'bg-red-100 text-red-700',
  expired: 'bg-slate-100 text-slate-500',
}

const MODE_COLORS: Record<CheckMode, string> = {
  verify: 'bg-purple-100 text-purple-700',
  research: 'bg-indigo-100 text-indigo-700',
  link: 'bg-teal-100 text-teal-700',
}

const STATUS_LABELS: Record<CheckStatus, string> = {
  queued: 'Queued',
  in_flight: 'In progress',
  ok: 'Answered',
  error: 'Error',
  expired: 'Expired',
}

const MODES: CheckMode[] = ['research', 'verify', 'link']

// `listJobs` fetches a single PAGE_LIMIT-sized page (see useCheck); the UI has no
// pagination. With CHECK retention at 14 days, that cap is assumed to comfortably
// cover a user's live questions, so paging is a deliberate v2 follow-up. When we
// do hit the cap the header counts and filter tabs only reflect the most-recent
// page, so we flag them as approximate instead of silently undercounting.

const inputClass = 'w-full px-3 py-1.5 border border-slate-200 rounded text-sm focus:outline-none focus:ring-1 focus:ring-primary'
const labelClass = 'block text-xs font-medium text-slate-500 mb-1'

// Pull a human-readable answer out of an arbitrary result dict: prefer a
// top-level `answer` string, otherwise pretty-print the whole object.
function renderAnswer(result: Record<string, unknown> | null): string | null {
  if (!result || Object.keys(result).length === 0) return null
  const answer = result.answer
  if (typeof answer === 'string') return answer
  return JSON.stringify(result, null, 2)
}

// --- Ask Form ---

interface AskFormProps {
  onAsk: (body: AskBody) => Promise<void>
  onCancel: () => void
  saving: boolean
  error: string | null
}

const AskForm = ({ onAsk, onCancel, saving, error }: AskFormProps) => {
  const [text, setText] = useState('')
  const [mode, setMode] = useState<CheckMode>('research')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!text.trim()) return
    await onAsk({ text: text.trim(), mode })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3 border border-slate-200 rounded-lg p-4 bg-slate-50">
      <div>
        <label className={labelClass}>Question</label>
        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          rows={3}
          placeholder="What would you like to verify, research, or link?"
          className={inputClass}
          autoFocus
        />
      </div>
      <div>
        <label className={labelClass}>Mode</label>
        <select value={mode} onChange={e => setMode(e.target.value as CheckMode)} className={inputClass}>
          {MODES.map(m => (
            <option key={m} value={m}>{m.charAt(0).toUpperCase() + m.slice(1)}</option>
          ))}
        </select>
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <div className="flex gap-2 pt-1">
        <button
          type="submit"
          disabled={saving || !text.trim()}
          className="bg-primary text-white py-1.5 px-4 rounded text-sm hover:bg-primary-dark disabled:bg-slate-300"
        >
          {saving ? 'Submitting...' : 'Ask'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={saving}
          className="bg-slate-100 text-slate-700 py-1.5 px-4 rounded text-sm hover:bg-slate-200"
        >
          Cancel
        </button>
      </div>
    </form>
  )
}

// --- Job Card ---

interface JobCardProps {
  job: CheckJob
  onDelete: (jobId: string) => Promise<void>
}

const JobCard = ({ job, onDelete }: JobCardProps) => {
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await onDelete(job.job_id)
    } catch {
      setDeleting(false)
      setConfirmDelete(false)
    }
  }

  // Only surface the answer for a successful job: an errored job can carry a
  // partial result, and showing both an Answer and the red error line is confusing.
  const answer = job.status === 'ok' ? renderAnswer(job.result) : null

  return (
    <li className="bg-white p-4 rounded-lg shadow-sm border-l-4 border-slate-200">
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span className={`px-2 py-0.5 rounded text-xs font-medium ${MODE_COLORS[job.mode] || 'bg-slate-100 text-slate-600'}`}>
              {job.mode}
            </span>
            <span className={`px-2 py-0.5 rounded text-xs font-medium ${STATUS_COLORS[job.status] || 'bg-slate-100 text-slate-600'}`}>
              {STATUS_LABELS[job.status] || job.status}
            </span>
          </div>

          <p className="text-sm font-medium text-slate-800 whitespace-pre-wrap break-words">{job.text}</p>

          {answer && (
            <div className="mt-2 border-t border-slate-100 pt-2">
              <p className="text-xs font-medium text-slate-400 mb-1">Answer</p>
              <p className="text-sm text-slate-700 whitespace-pre-wrap break-words">{answer}</p>
            </div>
          )}

          {job.status === 'error' && job.error && (
            <p className="text-sm text-red-600 mt-2 whitespace-pre-wrap break-words">{job.error}</p>
          )}

          <div className="flex gap-4 mt-2 text-xs text-slate-400 flex-wrap">
            <span>Asked: {formatRelativeTime(job.submitted_at)}</span>
            {job.completed_at && <span>Answered: {formatRelativeTime(job.completed_at)}</span>}
          </div>
        </div>

        <div className="shrink-0">
          {confirmDelete ? (
            <span className="flex items-center gap-1 text-sm">
              <span className="text-red-600">Delete?</span>
              <button onClick={handleDelete} disabled={deleting} className="text-red-600 font-medium hover:underline">
                {deleting ? '...' : 'Yes'}
              </button>
              <button onClick={() => setConfirmDelete(false)} className="text-slate-500 hover:underline">
                No
              </button>
            </span>
          ) : (
            <button
              onClick={() => setConfirmDelete(true)}
              className="bg-red-50 text-red-600 py-1.5 px-3 rounded text-sm hover:bg-red-100"
            >
              Delete
            </button>
          )}
        </div>
      </div>
    </li>
  )
}

// --- Main Page ---

type StatusFilter = 'all' | CheckStatus

const FILTERS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'queued', label: 'Queued' },
  { value: 'in_flight', label: 'In progress' },
  { value: 'ok', label: 'Answered' },
  { value: 'error', label: 'Error' },
  { value: 'expired', label: 'Expired' },
]

const Check = () => {
  const { listJobs, ask, deleteJob } = useCheck()
  const [jobs, setJobs] = useState<CheckJob[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')

  const [showAsk, setShowAsk] = useState(false)
  const [asking, setAsking] = useState(false)
  const [askError, setAskError] = useState<string | null>(null)

  // Always fetch the full list; filtering happens client-side for display so
  // the header counts stay accurate and switching tabs doesn't refetch/flash.
  const loadJobs = useCallback(async () => {
    setLoading(true)
    try {
      const data = await listJobs()
      setJobs(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load questions')
    } finally {
      setLoading(false)
    }
  }, [listJobs])

  useEffect(() => { loadJobs() }, [loadJobs])

  const handleAsk = async (body: AskBody) => {
    setAsking(true)
    setAskError(null)
    try {
      await ask(body)
      setShowAsk(false)
      await loadJobs()
    } catch (e) {
      setAskError(e instanceof Error ? e.message : 'Failed to submit question')
    } finally {
      setAsking(false)
    }
  }

  const handleDelete = async (jobId: string) => {
    await deleteJob(jobId)
    setJobs(prev => prev.filter(j => j.job_id !== jobId))
  }

  // Counts come from the full set; the displayed list is filtered in-memory.
  const answeredCount = jobs.filter(j => j.status === 'ok').length
  const pendingCount = jobs.filter(j => j.status === 'queued' || j.status === 'in_flight').length
  const visibleJobs = statusFilter === 'all' ? jobs : jobs.filter(j => j.status === statusFilter)

  // We only fetched the most-recent page; at the cap, counts are lower bounds
  // and older jobs aren't shown. Surface a "+" suffix and a notice so the
  // numbers don't read as exact totals.
  const atCap = jobs.length >= PAGE_LIMIT
  const countSuffix = atCap ? '+' : ''

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <header className="flex items-center gap-4 mb-6 pb-4 border-b border-slate-200">
        <Link to="/ui/dashboard" className="bg-slate-50 text-slate-600 border border-slate-200 py-2 px-4 rounded-md text-sm hover:bg-slate-100">
          Back
        </Link>
        <h1 className="text-2xl font-semibold text-slate-800 flex-1">Check Questions</h1>
        <div className="flex gap-3 text-sm">
          {pendingCount > 0 && <span className="text-blue-600 font-medium">{pendingCount}{countSuffix} pending</span>}
          <span className="text-green-600 font-medium">{answeredCount}{countSuffix} answered</span>
        </div>
      </header>

      <div className="space-y-4">
        <div className="flex gap-2 items-center flex-wrap">
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
            aria-label="Refresh question list"
          >
            &#8635;
          </button>
          <button onClick={() => setShowAsk(v => !v)} className="bg-primary text-white py-1.5 px-3 rounded text-sm hover:bg-primary-dark">
            {showAsk ? 'Close' : '+ New question'}
          </button>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg flex justify-between items-center">
            <p>{error}</p>
            <button onClick={() => loadJobs()} className="text-primary hover:underline">Retry</button>
          </div>
        )}

        {showAsk && (
          <div className="mb-4">
            <AskForm onAsk={handleAsk} onCancel={() => setShowAsk(false)} saving={asking} error={askError} />
          </div>
        )}

        {!loading && atCap && (
          <p className="text-xs text-slate-400">
            Showing the {PAGE_LIMIT} most recent questions.
          </p>
        )}

        {loading && <div className="text-center py-8 text-slate-500">Loading questions...</div>}

        {!loading && visibleJobs.length === 0 && (
          <div className="text-center py-12 text-slate-500 bg-white rounded-xl">
            {statusFilter === 'all' ? 'No questions yet' : `No ${statusFilter} questions`}
          </div>
        )}

        {!loading && visibleJobs.length > 0 && (
          <ul className="space-y-3">
            {visibleJobs.map(job => (
              <JobCard key={job.job_id} job={job} onDelete={handleDelete} />
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export default Check

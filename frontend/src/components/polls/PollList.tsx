import React, { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { usePolls } from '../../hooks/usePolls'
import type { Poll, PollStatus } from '../../hooks/usePolls'
import { formatShortDate } from '../../utils/timezones'

const STATUS_COLORS: Record<string, string> = {
  open: 'bg-green-100 text-green-700',
  closed: 'bg-slate-100 text-slate-600',
  finalized: 'bg-blue-100 text-blue-700',
  cancelled: 'bg-red-100 text-red-600',
}

export const PollList: React.FC = () => {
  const { listPolls, deletePoll } = usePolls()
  const [polls, setPolls] = useState<Poll[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<PollStatus | ''>('')
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null)
  const [actionLoading, setActionLoading] = useState<number | null>(null)

  const loadPolls = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listPolls(statusFilter || undefined)
      setPolls(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load polls')
    } finally {
      setLoading(false)
    }
  }, [listPolls, statusFilter])

  useEffect(() => {
    loadPolls()
  }, [loadPolls])

  const handleDelete = async (pollId: number) => {
    setActionLoading(pollId)
    try {
      await deletePoll(pollId)
      setPolls(polls.filter(p => p.id !== pollId))
      setDeleteConfirmId(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete poll')
    } finally {
      setActionLoading(null)
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 p-4 md:p-8">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-2xl font-semibold text-slate-800">Availability Polls</h1>
          <Link
            to="/ui/polls/new"
            className="bg-primary text-white py-2 px-4 rounded-lg font-medium hover:bg-primary-dark"
          >
            Create New Poll
          </Link>
        </div>

        <div className="mb-6">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as PollStatus | '')}
            className="py-2 px-3 border border-slate-200 rounded-lg text-sm bg-white min-w-[140px]"
          >
            <option value="">All Status</option>
            <option value="open">Open</option>
            <option value="closed">Closed</option>
            <option value="finalized">Finalized</option>
          </select>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg mb-4">
            {error}
          </div>
        )}

        {loading ? (
          <div className="text-center py-8 text-slate-500">Loading polls...</div>
        ) : polls.length === 0 ? (
          <div className="text-center py-12 bg-white rounded-xl shadow-sm">
            <p className="text-slate-500 mb-4">
              {statusFilter ? `No ${statusFilter} polls found.` : "You haven't created any polls yet."}
            </p>
            {!statusFilter && (
              <Link
                to="/ui/polls/new"
                className="inline-block bg-primary text-white py-2 px-4 rounded-lg font-medium hover:bg-primary-dark"
              >
                Create Your First Poll
              </Link>
            )}
          </div>
        ) : (
          <div className="space-y-4">
            {polls.map((poll) => (
              <div
                key={poll.id}
                className="bg-white p-5 rounded-xl shadow-sm border border-slate-100 hover:shadow-md transition-shadow"
              >
                <div className="flex items-start justify-between mb-2">
                  <h3 className="font-semibold text-slate-800">
                    <Link
                      to={`/ui/polls/results/${poll.slug}`}
                      className="hover:text-primary"
                    >
                      {poll.title}
                    </Link>
                  </h3>
                  <span className={`px-2 py-1 rounded text-xs font-medium capitalize ${STATUS_COLORS[poll.status] || 'bg-slate-100 text-slate-600'}`}>
                    {poll.status}
                  </span>
                </div>

                {poll.description && (
                  <p className="text-sm text-slate-600 mb-3 line-clamp-2">{poll.description}</p>
                )}

                <div className="flex gap-4 text-sm text-slate-500 mb-3">
                  <span>
                    {formatShortDate(poll.datetime_start)} - {formatShortDate(poll.datetime_end)}
                  </span>
                  <span>
                    {poll.response_count} {poll.response_count === 1 ? 'response' : 'responses'}
                  </span>
                </div>

                {poll.finalized_time && (
                  <div className="text-sm text-green-700 bg-green-50 px-3 py-2 rounded mb-3">
                    Meeting: {new Date(poll.finalized_time).toLocaleString()}
                  </div>
                )}

                <div className="flex gap-2 flex-wrap">
                  <Link
                    to={`/ui/polls/results/${poll.slug}`}
                    className="py-1.5 px-3 bg-slate-100 text-slate-700 rounded text-sm hover:bg-slate-200"
                  >
                    View Results
                  </Link>
                  <Link
                    to={`/ui/polls/edit/${poll.slug}`}
                    className="py-1.5 px-3 border border-slate-200 text-slate-600 rounded text-sm hover:bg-slate-50"
                  >
                    Edit
                  </Link>
                  <Link
                    to={`/ui/polls/respond/${poll.slug}`}
                    className="py-1.5 px-3 border border-slate-200 text-slate-600 rounded text-sm hover:bg-slate-50"
                  >
                    Share
                  </Link>

                  {deleteConfirmId === poll.id ? (
                    <div className="flex items-center gap-2 ml-auto">
                      <span className="text-sm text-slate-600">Delete?</span>
                      <button
                        type="button"
                        className="py-1.5 px-3 bg-red-600 text-white rounded text-sm hover:bg-red-700 disabled:bg-slate-300"
                        onClick={() => handleDelete(poll.id)}
                        disabled={actionLoading === poll.id}
                      >
                        {actionLoading === poll.id ? '...' : 'Yes'}
                      </button>
                      <button
                        type="button"
                        className="py-1.5 px-3 bg-slate-100 text-slate-700 rounded text-sm hover:bg-slate-200"
                        onClick={() => setDeleteConfirmId(null)}
                      >
                        No
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="py-1.5 px-3 border border-red-200 text-red-600 rounded text-sm hover:bg-red-50 ml-auto"
                      onClick={() => setDeleteConfirmId(poll.id)}
                      title="Delete poll"
                    >
                      Delete
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default PollList

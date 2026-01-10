import React, { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { usePolls } from '../../hooks/usePolls'
import type { Poll, PollStatus } from '../../hooks/usePolls'
import { formatShortDate } from '../../utils/timezones'

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
    <div className="polls-page">
      <div className="polls-header">
        <h1>Availability Polls</h1>
        <Link to="/ui/polls/new" className="btn btn-primary">
          Create New Poll
        </Link>
      </div>

      <div className="polls-filters">
        <select 
          value={statusFilter} 
          onChange={(e) => setStatusFilter(e.target.value as PollStatus | '')}
          className="status-filter"
        >
          <option value="">All Status</option>
          <option value="open">Open</option>
          <option value="closed">Closed</option>
          <option value="finalized">Finalized</option>
        </select>
      </div>

      {error && <div className="poll-error">{error}</div>}

      {loading ? (
        <div className="poll-loading">Loading polls...</div>
      ) : polls.length === 0 ? (
        <div className="polls-empty">
          <p>{statusFilter ? `No ${statusFilter} polls found.` : "You haven't created any polls yet."}</p>
          {!statusFilter && (
            <Link to="/ui/polls/new" className="btn btn-primary">
              Create Your First Poll
            </Link>
          )}
        </div>
      ) : (
        <div className="polls-list">
          {polls.map((poll) => (
            <div key={poll.id} className="poll-card">
              <div className="poll-card-header">
                <h3>
                  <Link to={`/ui/polls/results/${poll.slug}`}>{poll.title}</Link>
                </h3>
                <div className="poll-status-badge" data-status={poll.status}>
                  {poll.status}
                </div>
              </div>

              {poll.description && (
                <p className="poll-card-description">{poll.description}</p>
              )}

              <div className="poll-card-meta">
                <span className="poll-dates">
                  {formatShortDate(poll.datetime_start)} - {formatShortDate(poll.datetime_end)}
                </span>
                <span className="poll-responses">
                  {poll.response_count} {poll.response_count === 1 ? 'response' : 'responses'}
                </span>
              </div>

              {poll.finalized_time && (
                <div className="poll-card-finalized">
                  Meeting: {new Date(poll.finalized_time).toLocaleString()}
                </div>
              )}

              <div className="poll-card-actions">
                <Link
                  to={`/ui/polls/results/${poll.slug}`}
                  className="btn btn-secondary btn-sm"
                >
                  View Results
                </Link>
                <Link
                  to={`/ui/polls/edit/${poll.slug}`}
                  className="btn btn-outline btn-sm"
                >
                  Edit
                </Link>
                <Link
                  to={`/ui/polls/respond/${poll.slug}`}
                  className="btn btn-outline btn-sm"
                >
                  Share
                </Link>

                {/* Delete button */}
                {deleteConfirmId === poll.id ? (
                  <div className="delete-confirm">
                    <span>Delete?</span>
                    <button
                      type="button"
                      className="btn btn-danger btn-sm"
                      onClick={() => handleDelete(poll.id)}
                      disabled={actionLoading === poll.id}
                    >
                      {actionLoading === poll.id ? '...' : 'Yes'}
                    </button>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={() => setDeleteConfirmId(null)}
                    >
                      No
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="btn btn-outline btn-sm btn-icon-danger"
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
  )
}

export default PollList

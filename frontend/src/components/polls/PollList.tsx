import React, { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { usePolls } from '../../hooks/usePolls'
import type { Poll, PollStatus } from '../../hooks/usePolls'
import { formatShortDate } from '../../utils/timezones'
import { Button, ButtonLink, Card, LoadingState, Notice, Page, Select } from '@/components/shared/ui'
import { PollStatusBadge } from './PollStatusBadge'

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
    <Page
      title="Availability Polls"
      actions={<ButtonLink variant="primary" to="/ui/polls/new">Create New Poll</ButtonLink>}
      width="narrow"
    >
        <div className="mb-6">
          <Select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as PollStatus | '')}
            className="w-auto"
            aria-label="Status"
          >
            <option value="">All statuses</option>
            <option value="open">Open</option>
            <option value="closed">Closed</option>
            <option value="finalized">Finalized</option>
          </Select>
        </div>

        {error && (
          <Notice tone="error" className="mb-4">
            {error}
          </Notice>
        )}

        {loading ? (
          <LoadingState message="Loading polls..." />
        ) : polls.length === 0 ? (
          <Card flush className="text-center py-12">
            <p className="text-slate-500 mb-4">
              {statusFilter ? `No ${statusFilter} polls found.` : "You haven't created any polls yet."}
            </p>
            {!statusFilter && (
              <ButtonLink variant="primary"
                to="/ui/polls/new"
                className="inline-block"
              >
                Create Your First Poll
              </ButtonLink>
            )}
          </Card>
        ) : (
          <div className="space-y-4">
            {polls.map((poll) => (
              <Card flush
                key={poll.id}
                className="p-5 hover:shadow-md transition-shadow"
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
                  <PollStatusBadge status={poll.status} />
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
                  <ButtonLink size="sm"
                    to={`/ui/polls/results/${poll.slug}`}
                  >
                    View Results
                  </ButtonLink>
                  <ButtonLink size="sm"
                    to={`/ui/polls/edit/${poll.slug}`}
                  >
                    Edit
                  </ButtonLink>
                  <ButtonLink size="sm"
                    to={`/ui/polls/respond/${poll.slug}`}
                  >
                    Share
                  </ButtonLink>

                  {deleteConfirmId === poll.id ? (
                    <div className="flex items-center gap-2 ml-auto">
                      <span className="text-sm text-slate-600">Delete?</span>
                      <Button variant="dangerSolid" size="sm"
                        type="button"
                        onClick={() => handleDelete(poll.id)}
                        disabled={actionLoading === poll.id}
                      >
                        {actionLoading === poll.id ? '...' : 'Yes'}
                      </Button>
                      <Button size="sm"
                        type="button"
                        onClick={() => setDeleteConfirmId(null)}
                      >
                        No
                      </Button>
                    </div>
                  ) : (
                    <Button variant="danger" size="sm"
                      type="button"
                      className="ml-auto"
                      onClick={() => setDeleteConfirmId(poll.id)}
                      title="Delete poll"
                    >
                      Delete
                    </Button>
                  )}
                </div>
              </Card>
            ))}
          </div>
        )}
      </Page>
  )
}

export default PollList

import React, { useState, useEffect, useCallback } from 'react'
import { usePolls } from '../../hooks/usePolls'
import type { Poll, PollStatus } from '../../hooks/usePolls'
import { formatShortDate } from '../../utils/timezones'
import { Button, ButtonLink, ConfirmButton, EmptyState, List, LoadingState, Notice, Page, Row, Select, Toolbar, useActionNotice, copyText } from '@/components/shared/ui'
import { PollStatusBadge } from './PollStatusBadge'

export const PollList: React.FC = () => {
  const { listPolls, deletePoll } = usePolls()
  const [polls, setPolls] = useState<Poll[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<PollStatus | ''>('')
  const [actionLoading, setActionLoading] = useState<number | null>(null)
  const { run, notice } = useActionNotice()

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
    if (await run(() => deletePoll(pollId))) setPolls(polls.filter(p => p.id !== pollId))
    setActionLoading(null)
  }

  return (
    <Page
      title="Polls"
      actions={<ButtonLink variant="primary" to="/ui/polls/new">New poll</ButtonLink>}
      width="default"
    >
      <Toolbar>
        <Select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as PollStatus | '')} className="w-auto" aria-label="Status">
          <option value="">All statuses</option>
          <option value="open">Open</option>
          <option value="closed">Closed</option>
          <option value="finalized">Finalized</option>
        </Select>
      </Toolbar>

      {error && <Notice tone="error" onRetry={loadPolls}>{error}</Notice>}
      {notice}

      {loading ? (
        <LoadingState message="Loading polls..." />
      ) : polls.length === 0 ? (
        <EmptyState
          message={statusFilter ? `No ${statusFilter} polls found.` : "You haven't created any polls yet."}
          action={!statusFilter && <ButtonLink variant="primary" to="/ui/polls/new">New poll</ButtonLink>}
        />
      ) : (
        <List>
          {polls.map((poll) => (
            <Row
              key={poll.id}
              title={poll.title}
              to={`/ui/polls/results/${poll.slug}`}
              badges={<PollStatusBadge status={poll.status} />}
              meta={[
                `${formatShortDate(poll.datetime_start)} - ${formatShortDate(poll.datetime_end)}`,
                `${poll.response_count} ${poll.response_count === 1 ? 'response' : 'responses'}`,
                poll.finalized_time && `Meeting: ${new Date(poll.finalized_time).toLocaleString()}`,
              ]}
              actions={<>
                <ButtonLink size="sm" to={`/ui/polls/edit/${poll.slug}`}>Edit</ButtonLink>
                <Button size="sm" onClick={() => run(() => copyText(`${window.location.origin}/ui/polls/respond/${poll.slug}`), 'Link copied')}>
                  Copy link
                </Button>
                <ConfirmButton confirm={`Delete the poll "${poll.title}"?`} onConfirm={() => handleDelete(poll.id)}
                  disabled={actionLoading === poll.id} title="Delete poll" />
              </>}
            >
              {poll.description && <p className="text-slate-600 line-clamp-2">{poll.description}</p>}
            </Row>
          ))}
        </List>
      )}
      </Page>
  )
}

export default PollList

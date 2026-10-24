import React, { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { getPollResults, usePolls } from '../../hooks/usePolls'
import type { Poll, SlotAggregation } from '../../hooks/usePolls'
import PollGrid from './PollGrid'
import {
  getBrowserTimezone,
  COMMON_TIMEZONES,
  formatTimezone,
  formatSlotTime,
} from '../../utils/timezones'
import { useAuth } from '../../hooks/useAuth'
import { Button, ButtonLink, Card, ConfirmButton, FormActions, List, Modal, Notice, Page, Row, Section, Select, Stat, StatGrid, useActionNotice, copyText } from '@/components/shared/ui'
import { PollStatusBadge } from './PollStatusBadge'

export const PollResults: React.FC = () => {
  const { slug } = useParams<{ slug: string }>()
  const { isAuthenticated } = useAuth()
  const { closePoll, finalizePoll, cancelPoll, updatePoll } = usePolls()
  const { run, notice } = useActionNotice()

  const [poll, setPoll] = useState<Poll | null>(null)
  const [responseCount, setResponseCount] = useState(0)
  const [aggregation, setAggregation] = useState<SlotAggregation[]>([])
  const [bestSlots, setBestSlots] = useState<SlotAggregation[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [displayTimezone, setDisplayTimezone] = useState(getBrowserTimezone())
  const [isOwner, setIsOwner] = useState(false)

  const [showFinalizeModal, setShowFinalizeModal] = useState(false)
  const [selectedFinalizeTime, setSelectedFinalizeTime] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState(false)

  useEffect(() => {
    if (!slug) return

    const loadResults = async () => {
      setLoading(true)
      setError(null)
      try {
        const resultsData = await getPollResults(slug)
        setPoll(resultsData.poll)
        setResponseCount(resultsData.response_count)
        setAggregation(resultsData.aggregated || [])
        setBestSlots(resultsData.best_slots || [])
        setIsOwner(resultsData.is_owner)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load results')
      } finally {
        setLoading(false)
      }
    }

    loadResults()
  }, [slug])


  const formatSlot = (slot: SlotAggregation) =>
    formatSlotTime(slot.slot_start, slot.slot_end, displayTimezone)

  const manage = async (action: () => Promise<Partial<Poll>>, status: Poll['status'], extra: Partial<Poll> = {}) => {
    if (!poll) return false
    setActionLoading(true)
    const ok = await run(async () => {
      const updated = await action()
      setPoll({ ...poll, ...updated, status, ...extra })
    })
    setActionLoading(false)
    return ok
  }

  const handleFinalizePoll = async () => {
    if (!poll || !selectedFinalizeTime) return
    const ok = await manage(() => finalizePoll(poll.id, selectedFinalizeTime), 'finalized', { finalized_time: selectedFinalizeTime })
    if (ok) setShowFinalizeModal(false)
  }

  const displayBestSlots = bestSlots.slice(0, 5)
  const back = isAuthenticated ? { to: '/ui/polls', label: 'Polls' } : undefined

  if (loading || error || !poll) {
    return <Page title="Poll results" back={back} loading={loading && 'Loading results...'} error={loading ? null : error ?? 'Poll not found'} />
  }

  const canFinalize = poll.status === 'open' || poll.status === 'closed'

  return (
    <Page
      title={<>{poll.title}<PollStatusBadge status={poll.status} /></>}
      back={back}
      description={poll.description}
      actions={<>
        <div className="flex items-center gap-2">
          <label htmlFor="displayTimezone" className="text-sm text-slate-600">
            Timezone
          </label>
          <Select
            id="displayTimezone"
            className="w-auto"
            value={displayTimezone}
            onChange={(e) => setDisplayTimezone(e.target.value)}
          >
            {!COMMON_TIMEZONES.includes(displayTimezone) && (
              <option value={displayTimezone}>{formatTimezone(displayTimezone)}</option>
            )}
            {COMMON_TIMEZONES.map(tz => (
              <option key={tz} value={tz}>{formatTimezone(tz)}</option>
            ))}
          </Select>
        </div>

        {isOwner && (
          <div className="flex flex-wrap gap-2">
            {canFinalize && <Button size="sm" onClick={() => setShowFinalizeModal(true)} disabled={actionLoading}>Finalize poll</Button>}
            {poll.status === 'open' && (
              <Button size="sm" onClick={() => manage(() => closePoll(poll.id), 'closed')} disabled={actionLoading}>Close poll</Button>
            )}
            {poll.status === 'closed' && (
              <Button size="sm" onClick={() => manage(() => updatePoll({ poll_id: poll.id, status: 'open' }), 'open')} disabled={actionLoading}>Reopen poll</Button>
            )}
            <ButtonLink size="sm" to={`/ui/polls/edit/${slug}`}>Edit poll</ButtonLink>
            {poll.status !== 'cancelled' && (
              <ConfirmButton disabled={actionLoading}
                confirm={`Cancel "${poll.title}"? It will stop accepting responses.`}
                onConfirm={async () => { await manage(() => cancelPoll(poll.id), 'cancelled') }}>
                Cancel poll
              </ConfirmButton>
            )}
          </div>
        )}
      </>}
    >
        {notice}

        {showFinalizeModal && (
          <Modal title="Finalize poll" onClose={() => setShowFinalizeModal(false)} size="md">
            {bestSlots.length > 0 ? (
              <>
                <p className="text-slate-600 mb-4">Select a time to schedule the meeting:</p>
                <div className="space-y-2 max-h-64 overflow-y-auto mb-4">
                  {bestSlots.slice(0, 10).map((slot, i) => (
                    <label key={i} className="flex items-center gap-3 p-3 border border-slate-200 rounded-lg cursor-pointer hover:bg-slate-50">
                      <input
                        type="radio"
                        name="finalize_time"
                        value={slot.slot_start}
                        checked={selectedFinalizeTime === slot.slot_start}
                        onChange={() => setSelectedFinalizeTime(slot.slot_start)}
                      />
                      <span className="flex-1 text-sm">{formatSlot(slot)}</span>
                      <span className="text-xs text-slate-500">
                        {slot.available_count} available
                        {slot.if_needed_count > 0 && ` (+${slot.if_needed_count})`}
                      </span>
                    </label>
                  ))}
                </div>
              </>
            ) : (
              <p className="text-slate-600 mb-4">No responses yet, so there is no time to pick. Share the poll and finalize once people have added their availability.</p>
            )}

            <FormActions>
              <Button type="button" onClick={() => setShowFinalizeModal(false)}>
                {bestSlots.length > 0 ? 'Cancel' : 'Close'}
              </Button>
              {bestSlots.length > 0 && (
                <Button variant="primary" type="button" onClick={handleFinalizePoll} disabled={!selectedFinalizeTime || actionLoading}>
                  {actionLoading ? 'Finalizing...' : 'Finalize'}
                </Button>
              )}
            </FormActions>
          </Modal>
        )}

        {/* Finalized Banner */}
        {poll.finalized_time && (
          <Notice tone="success">
            <h2 className="font-semibold">Meeting scheduled</h2>
            <p className="text-lg">
              {new Date(poll.finalized_time).toLocaleString('en-US', {
                timeZone: displayTimezone,
                weekday: 'long',
                year: 'numeric',
                month: 'long',
                day: 'numeric',
                hour: 'numeric',
                minute: '2-digit',
              })}
            </p>
          </Notice>
        )}

        {/* Summary Stats */}
        <StatGrid>
          <Stat label="Responses" value={responseCount} />
          {displayBestSlots.length > 0 && <Stat label="Max available" value={displayBestSlots[0].available_count} />}
        </StatGrid>

        {displayBestSlots.length > 0 && (
          <Section title="Best times">
            <List>
              {displayBestSlots.map((slot, i) => (
                <Row key={i} title={formatSlot(slot)}
                  meta={[`${slot.available_count} available`, slot.if_needed_count > 0 && `+${slot.if_needed_count} if needed`]}>
                  {slot.respondents.length > 0 && <p className="text-sm text-slate-600">{slot.respondents.join(', ')}</p>}
                </Row>
              ))}
            </List>
          </Section>
        )}

        {/* Availability Heatmap */}
        <Card title="Availability heatmap">
          <PollGrid
            datetimeStart={new Date(poll.datetime_start)}
            datetimeEnd={new Date(poll.datetime_end)}
            slotDurationMinutes={poll.slot_duration_minutes}
            displayTimezone={displayTimezone}
            selectedSlots={[]}
            aggregatedData={aggregation}
            totalResponses={responseCount}
            readonly={true}
          />

          <div className="flex items-center justify-center gap-2 mt-4 text-sm text-slate-600">
            <span>Fewer</span>
            <div className="w-24 h-3 bg-gradient-to-r from-slate-100 via-green-200 to-green-500 rounded" />
            <span>More available</span>
          </div>
        </Card>

        {/* Respondents */}
        <Card title={<>Respondents ({responseCount})</>}>
          {responseCount === 0 ? (
            <p className="text-slate-600">No responses yet.</p>
          ) : (
            <p className="text-slate-600">
              {responseCount} {responseCount === 1 ? 'person has' : 'people have'} responded to this poll.
            </p>
          )}
        </Card>

        {/* Actions */}
        <div className="flex gap-3 flex-wrap">
          {poll.status === 'open' && (
            <ButtonLink variant="primary"
              to={`/ui/polls/respond/${slug}`}
            >
              Add your availability
            </ButtonLink>
          )}
          <Button
            type="button"
            onClick={() => run(() => copyText(`${window.location.origin}/ui/polls/respond/${slug}`), 'Link copied')}
          >
            Copy share link
          </Button>
        </div>
    </Page>
  )
}

export default PollResults

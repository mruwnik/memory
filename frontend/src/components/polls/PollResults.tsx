import React, { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
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
import { Button, ButtonLink, Card, List, Modal, Notice, Page, Row, Section, Select, Stat, StatGrid } from '@/components/shared/ui'
import { PollStatusBadge } from './PollStatusBadge'

export const PollResults: React.FC = () => {
  const { slug } = useParams<{ slug: string }>()
  const { isAuthenticated } = useAuth()
  const { closePoll, finalizePoll, cancelPoll, updatePoll } = usePolls()

  const [poll, setPoll] = useState<Poll | null>(null)
  const [responseCount, setResponseCount] = useState(0)
  const [aggregation, setAggregation] = useState<SlotAggregation[]>([])
  const [bestSlots, setBestSlots] = useState<SlotAggregation[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [displayTimezone, setDisplayTimezone] = useState(getBrowserTimezone())

  // Management state
  const [showManageMenu, setShowManageMenu] = useState(false)
  const [showFinalizeModal, setShowFinalizeModal] = useState(false)
  const [showCancelConfirm, setShowCancelConfirm] = useState(false)
  const [selectedFinalizeTime, setSelectedFinalizeTime] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

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
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load results')
      } finally {
        setLoading(false)
      }
    }

    loadResults()
  }, [slug])

  // Helper to format a slot from aggregation data
  const formatSlot = (slot: SlotAggregation) =>
    formatSlotTime(slot.slot_start, slot.slot_end, displayTimezone)

  const handleClosePoll = async () => {
    if (!poll) return
    setActionLoading(true)
    setActionError(null)
    try {
      const updated = await closePoll(poll.id)
      setPoll({ ...poll, ...updated, status: 'closed' })
      setShowManageMenu(false)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to close poll')
    } finally {
      setActionLoading(false)
    }
  }

  const handleReopenPoll = async () => {
    if (!poll) return
    setActionLoading(true)
    setActionError(null)
    try {
      const updated = await updatePoll({ poll_id: poll.id, status: 'open' })
      setPoll({ ...poll, ...updated, status: 'open' })
      setShowManageMenu(false)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to reopen poll')
    } finally {
      setActionLoading(false)
    }
  }

  const handleFinalizePoll = async () => {
    if (!poll || !selectedFinalizeTime) return
    setActionLoading(true)
    setActionError(null)
    try {
      const updated = await finalizePoll(poll.id, selectedFinalizeTime)
      setPoll({ ...poll, ...updated, status: 'finalized', finalized_time: selectedFinalizeTime })
      setShowFinalizeModal(false)
      setShowManageMenu(false)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to finalize poll')
    } finally {
      setActionLoading(false)
    }
  }

  const handleCancelPoll = async () => {
    if (!poll) return
    setActionLoading(true)
    setActionError(null)
    try {
      const updated = await cancelPoll(poll.id)
      setPoll({ ...poll, ...updated, status: 'cancelled' })
      setShowCancelConfirm(false)
      setShowManageMenu(false)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to cancel poll')
    } finally {
      setActionLoading(false)
    }
  }

  // Limit best slots to 5 for display
  const displayBestSlots = bestSlots.slice(0, 5)

  if (loading || error || !poll) {
    return <Page title="Poll results" loading={loading && 'Loading results...'} error={loading ? null : error ?? 'Poll not found'} />
  }

  return (
    <Page
      title={<>{poll.title}<PollStatusBadge status={poll.status} /></>}
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

        {/* Poll Management Dropdown - only show for authenticated users */}
        {isAuthenticated && (
          <div className="relative ml-auto">
            <Button size="sm"
              type="button"
              onClick={() => setShowManageMenu(!showManageMenu)}
            >
              Manage Poll ▾
            </Button>
            {showManageMenu && (
              <div className="absolute right-0 mt-1 w-48 bg-white border border-slate-200 rounded-lg shadow-lg z-10">
                {poll.status === 'open' && (
                  <>
                    <button
                      type="button"
                      className="w-full text-left px-4 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                      onClick={() => {
                        setShowFinalizeModal(true)
                        setShowManageMenu(false)
                      }}
                      disabled={actionLoading}
                    >
                      Finalize poll
                    </button>
                    <button
                      type="button"
                      className="w-full text-left px-4 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                      onClick={handleClosePoll}
                      disabled={actionLoading}
                    >
                      Close poll
                    </button>
                  </>
                )}
                {poll.status === 'closed' && (
                  <>
                    <button
                      type="button"
                      className="w-full text-left px-4 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                      onClick={() => {
                        setShowFinalizeModal(true)
                        setShowManageMenu(false)
                      }}
                      disabled={actionLoading}
                    >
                      Finalize poll
                    </button>
                    <button
                      type="button"
                      className="w-full text-left px-4 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                      onClick={handleReopenPoll}
                      disabled={actionLoading}
                    >
                      Reopen poll
                    </button>
                  </>
                )}
                <Link
                  to={`/ui/polls/edit/${slug}`}
                  className="block px-4 py-2 text-sm text-slate-700 hover:bg-slate-50"
                  onClick={() => setShowManageMenu(false)}
                >
                  Edit poll
                </Link>
                {poll.status !== 'cancelled' && (
                  <>
                    <hr className="my-1 border-slate-200" />
                    <button
                      type="button"
                      className="w-full text-left px-4 py-2 text-sm text-red-600 hover:bg-red-50 disabled:opacity-50"
                      onClick={() => {
                        setShowCancelConfirm(true)
                        setShowManageMenu(false)
                      }}
                      disabled={actionLoading}
                    >
                      Cancel poll
                    </button>
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </>}
    >
        {actionError && <Notice tone="error">{actionError}</Notice>}

        {/* Finalize Modal */}
        {showFinalizeModal && (
          <Modal title="Finalize poll" onClose={() => setShowFinalizeModal(false)} size="md">
            <p className="text-slate-600 mb-4">Select a time to schedule the meeting:</p>

              {bestSlots.length > 0 ? (
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
              ) : (
                <p className="text-slate-500 text-sm mb-4">No responses yet. You can still finalize with a custom time.</p>
              )}

              <div className="flex justify-end gap-3">
                <Button
                  type="button"
                  onClick={() => setShowFinalizeModal(false)}
                >
                  Cancel
                </Button>
                <Button variant="primary"
                  type="button"
                  onClick={handleFinalizePoll}
                  disabled={!selectedFinalizeTime || actionLoading}
                >
                  {actionLoading ? 'Finalizing...' : 'Finalize'}
                </Button>
              </div>
          </Modal>
        )}

        {/* Cancel Confirmation Modal */}
        {showCancelConfirm && (
          <Modal title="Cancel poll" onClose={() => setShowCancelConfirm(false)} size="md">
            <p className="text-slate-600 mb-2">Are you sure you want to cancel "{poll.title}"?</p>
              <p className="text-sm text-amber-600 mb-4">The poll will be marked as cancelled and will no longer accept responses.</p>

              <div className="flex justify-end gap-3">
                <Button
                  type="button"
                  onClick={() => setShowCancelConfirm(false)}
                >
                  Keep open
                </Button>
                <Button variant="dangerSolid"
                  type="button"
                  onClick={handleCancelPoll}
                  disabled={actionLoading}
                >
                  {actionLoading ? 'Cancelling...' : 'Cancel poll'}
                </Button>
              </div>
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
                  meta={[`${slot.available_count} available`, slot.if_needed_count > 0 && `+${slot.if_needed_count} if needed`]} />
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
            <p className="text-slate-600">No responses yet. Be the first to add your availability!</p>
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
            onClick={() => {
              const url = `${window.location.origin}/ui/polls/respond/${slug}`
              navigator.clipboard.writeText(url)
            }}
          >
            Copy share link
          </Button>
        </div>
    </Page>
  )
}

export default PollResults

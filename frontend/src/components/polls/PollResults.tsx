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

const STATUS_COLORS: Record<string, string> = {
  open: 'bg-green-100 text-green-700',
  closed: 'bg-slate-100 text-slate-600',
  finalized: 'bg-blue-100 text-blue-700',
  cancelled: 'bg-red-100 text-red-600',
}

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

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50 p-4 md:p-8">
        <div className="text-center py-8 text-slate-500">Loading results...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-slate-50 p-4 md:p-8">
        <div className="max-w-4xl mx-auto">
          <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg">
            {error}
          </div>
        </div>
      </div>
    )
  }

  if (!poll) {
    return (
      <div className="min-h-screen bg-slate-50 p-4 md:p-8">
        <div className="max-w-4xl mx-auto">
          <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg">
            Poll not found
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-50 p-4 md:p-8">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="bg-white p-6 rounded-xl shadow-sm mb-6">
          <div className="flex items-start justify-between mb-2">
            <h1 className="text-2xl font-semibold text-slate-800">{poll.title}</h1>
            <span className={`px-2 py-1 rounded text-xs font-medium capitalize ${STATUS_COLORS[poll.status] || 'bg-slate-100 text-slate-600'}`}>
              {poll.status}
            </span>
          </div>
          {poll.description && <p className="text-slate-600 mb-4">{poll.description}</p>}

          <div className="flex flex-wrap items-center gap-4">
            <div>
              <label htmlFor="displayTimezone" className="text-sm text-slate-500 mr-2">
                Timezone
              </label>
              <select
                id="displayTimezone"
                value={displayTimezone}
                onChange={(e) => setDisplayTimezone(e.target.value)}
                className="py-1.5 px-2 border border-slate-200 rounded text-sm bg-white"
              >
                {!COMMON_TIMEZONES.includes(displayTimezone) && (
                  <option value={displayTimezone}>{formatTimezone(displayTimezone)}</option>
                )}
                {COMMON_TIMEZONES.map(tz => (
                  <option key={tz} value={tz}>{formatTimezone(tz)}</option>
                ))}
              </select>
            </div>

            {/* Poll Management Dropdown - only show for authenticated users */}
            {isAuthenticated && (
              <div className="relative ml-auto">
                <button
                  type="button"
                  className="py-1.5 px-3 bg-slate-100 text-slate-700 rounded text-sm hover:bg-slate-200"
                  onClick={() => setShowManageMenu(!showManageMenu)}
                >
                  Manage Poll ▾
                </button>
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
                          Finalize Poll
                        </button>
                        <button
                          type="button"
                          className="w-full text-left px-4 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                          onClick={handleClosePoll}
                          disabled={actionLoading}
                        >
                          Close Poll
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
                          Finalize Poll
                        </button>
                        <button
                          type="button"
                          className="w-full text-left px-4 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                          onClick={handleReopenPoll}
                          disabled={actionLoading}
                        >
                          Reopen Poll
                        </button>
                      </>
                    )}
                    <Link
                      to={`/ui/polls/edit/${slug}`}
                      className="block px-4 py-2 text-sm text-slate-700 hover:bg-slate-50"
                      onClick={() => setShowManageMenu(false)}
                    >
                      Edit Poll
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
                          Cancel Poll
                        </button>
                      </>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          {actionError && (
            <div className="mt-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
              {actionError}
            </div>
          )}
        </div>

        {/* Finalize Modal */}
        {showFinalizeModal && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={() => setShowFinalizeModal(false)}>
            <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-6" onClick={e => e.stopPropagation()}>
              <h2 className="text-lg font-semibold text-slate-800 mb-2">Finalize Poll</h2>
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
                        className="text-primary"
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
                <button
                  type="button"
                  className="py-2 px-4 border border-slate-200 rounded-lg text-slate-600 hover:bg-slate-50"
                  onClick={() => setShowFinalizeModal(false)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="py-2 px-4 bg-primary text-white rounded-lg font-medium hover:bg-primary-dark disabled:bg-slate-300"
                  onClick={handleFinalizePoll}
                  disabled={!selectedFinalizeTime || actionLoading}
                >
                  {actionLoading ? 'Finalizing...' : 'Finalize'}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Cancel Confirmation Modal */}
        {showCancelConfirm && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={() => setShowCancelConfirm(false)}>
            <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-6" onClick={e => e.stopPropagation()}>
              <h2 className="text-lg font-semibold text-slate-800 mb-2">Cancel Poll</h2>
              <p className="text-slate-600 mb-2">Are you sure you want to cancel "{poll.title}"?</p>
              <p className="text-sm text-amber-600 mb-4">The poll will be marked as cancelled and will no longer accept responses.</p>

              <div className="flex justify-end gap-3">
                <button
                  type="button"
                  className="py-2 px-4 border border-slate-200 rounded-lg text-slate-600 hover:bg-slate-50"
                  onClick={() => setShowCancelConfirm(false)}
                >
                  Keep Open
                </button>
                <button
                  type="button"
                  className="py-2 px-4 bg-red-600 text-white rounded-lg font-medium hover:bg-red-700 disabled:bg-slate-300"
                  onClick={handleCancelPoll}
                  disabled={actionLoading}
                >
                  {actionLoading ? 'Cancelling...' : 'Cancel Poll'}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Finalized Banner */}
        {poll.finalized_time && (
          <div className="bg-green-50 border border-green-200 rounded-xl p-6 mb-6 text-center">
            <h2 className="text-lg font-semibold text-green-800 mb-2">Meeting Scheduled</h2>
            <p className="text-green-700 text-lg">
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
          </div>
        )}

        {/* Summary Stats */}
        <div className="grid grid-cols-2 gap-4 mb-6">
          <div className="bg-white p-4 rounded-xl shadow-sm text-center">
            <div className="text-3xl font-bold text-primary">{responseCount}</div>
            <div className="text-sm text-slate-500">Responses</div>
          </div>
          {displayBestSlots.length > 0 && (
            <div className="bg-white p-4 rounded-xl shadow-sm text-center">
              <div className="text-3xl font-bold text-primary">{displayBestSlots[0].available_count}</div>
              <div className="text-sm text-slate-500">Max Available</div>
            </div>
          )}
        </div>

        {/* Best Times */}
        {displayBestSlots.length > 0 && (
          <div className="bg-white p-6 rounded-xl shadow-sm mb-6">
            <h3 className="text-lg font-semibold text-slate-800 mb-4">Best Times</h3>
            <ul className="space-y-2">
              {displayBestSlots.map((slot, i) => (
                <li key={i} className="flex items-center justify-between p-3 bg-slate-50 rounded-lg">
                  <span className="font-medium text-slate-700">{formatSlot(slot)}</span>
                  <span className="text-sm text-slate-500">
                    {slot.available_count} available
                    {slot.if_needed_count > 0 && ` (+${slot.if_needed_count} if needed)`}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Availability Heatmap */}
        <div className="bg-white p-6 rounded-xl shadow-sm mb-6">
          <h3 className="text-lg font-semibold text-slate-800 mb-4">Availability Heatmap</h3>
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

          <div className="flex items-center justify-center gap-2 mt-4 text-sm text-slate-500">
            <span>Fewer</span>
            <div className="w-24 h-3 bg-gradient-to-r from-slate-100 via-green-200 to-green-500 rounded" />
            <span>More Available</span>
          </div>
        </div>

        {/* Respondents */}
        <div className="bg-white p-6 rounded-xl shadow-sm mb-6">
          <h3 className="text-lg font-semibold text-slate-800 mb-2">Respondents ({responseCount})</h3>
          {responseCount === 0 ? (
            <p className="text-slate-500">No responses yet. Be the first to add your availability!</p>
          ) : (
            <p className="text-slate-600">
              {responseCount} {responseCount === 1 ? 'person has' : 'people have'} responded to this poll.
            </p>
          )}
        </div>

        {/* Actions */}
        <div className="flex gap-3 flex-wrap">
          {poll.status === 'open' && (
            <Link
              to={`/ui/polls/respond/${slug}`}
              className="py-2 px-4 bg-primary text-white rounded-lg font-medium hover:bg-primary-dark"
            >
              Add Your Availability
            </Link>
          )}
          <button
            type="button"
            className="py-2 px-4 bg-slate-100 text-slate-700 rounded-lg hover:bg-slate-200"
            onClick={() => {
              const url = `${window.location.origin}/ui/polls/respond/${slug}`
              navigator.clipboard.writeText(url)
            }}
          >
            Copy Share Link
          </button>
        </div>
      </div>
    </div>
  )
}

export default PollResults

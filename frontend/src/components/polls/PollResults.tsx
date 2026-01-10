import React, { useState, useEffect } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { getPollResults, usePolls } from '../../hooks/usePolls'
import type { Poll, SlotAggregation } from '../../hooks/usePolls'
import PollGrid from './PollGrid'
import {
  getBrowserTimezone,
  COMMON_TIMEZONES,
  formatTimezone,
  formatSlotTime,
  formatDateTimeInTimezone,
} from '../../utils/timezones'
import { useAuth } from '../../hooks/useAuth'

export const PollResults: React.FC = () => {
  const { slug } = useParams<{ slug: string }>()
  const navigate = useNavigate()
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
      <div className="poll-results-page">
        <div className="poll-loading">Loading results...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="poll-results-page">
        <div className="poll-error">{error}</div>
      </div>
    )
  }

  if (!poll) {
    return (
      <div className="poll-results-page">
        <div className="poll-error">Poll not found</div>
      </div>
    )
  }

  return (
    <div className="poll-results-page">
      <div className="poll-header">
        <div className="poll-header-top">
          <h1>{poll.title}</h1>
          <div className="poll-status-badge" data-status={poll.status}>
            {poll.status}
          </div>
        </div>
        {poll.description && <p className="poll-description">{poll.description}</p>}
        
        <div className="poll-header-controls">
          <div className="poll-form-group poll-timezone-selector">
            <label htmlFor="displayTimezone">Timezone</label>
            <select
              id="displayTimezone"
              value={displayTimezone}
              onChange={(e) => setDisplayTimezone(e.target.value)}
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
            <div className="poll-manage-dropdown">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setShowManageMenu(!showManageMenu)}
              >
                Manage Poll ▾
              </button>
              {showManageMenu && (
                <div className="poll-manage-menu">
                  {poll.status === 'open' && (
                    <>
                      <button
                        type="button"
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
                        onClick={handleReopenPoll}
                        disabled={actionLoading}
                      >
                        Reopen Poll
                      </button>
                    </>
                  )}
                  <Link
                    to={`/ui/polls/edit/${slug}`}
                    onClick={() => setShowManageMenu(false)}
                  >
                    Edit Poll
                  </Link>
                  {poll.status !== 'cancelled' && (
                    <>
                      <hr />
                      <button
                        type="button"
                        className="danger"
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
          <div className="poll-error" style={{ marginTop: '1rem' }}>{actionError}</div>
        )}
      </div>

      {/* Finalize Modal */}
      {showFinalizeModal && (
        <div className="modal-overlay" onClick={() => setShowFinalizeModal(false)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <h2>Finalize Poll</h2>
            <p>Select a time to schedule the meeting:</p>
            
            {bestSlots.length > 0 ? (
              <div className="finalize-time-options">
                {bestSlots.slice(0, 10).map((slot, i) => (
                  <label key={i} className="finalize-time-option">
                    <input
                      type="radio"
                      name="finalize_time"
                      value={slot.slot_start}
                      checked={selectedFinalizeTime === slot.slot_start}
                      onChange={() => setSelectedFinalizeTime(slot.slot_start)}
                    />
                    <span className="option-time">{formatSlot(slot)}</span>
                    <span className="option-count">
                      {slot.available_count} available
                      {slot.if_needed_count > 0 && ` (+${slot.if_needed_count})`}
                    </span>
                  </label>
                ))}
              </div>
            ) : (
              <p className="no-slots-message">No responses yet. You can still finalize with a custom time.</p>
            )}

            <div className="modal-actions">
              <button 
                type="button"
                className="btn btn-secondary"
                onClick={() => setShowFinalizeModal(false)}
              >
                Cancel
              </button>
              <button 
                type="button"
                className="btn btn-primary"
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
        <div className="modal-overlay" onClick={() => setShowCancelConfirm(false)}>
          <div className="modal-content" onClick={e => e.stopPropagation()}>
            <h2>Cancel Poll</h2>
            <p>Are you sure you want to cancel "{poll.title}"?</p>
            <p className="warning-text">The poll will be marked as cancelled and will no longer accept responses.</p>

            <div className="modal-actions">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setShowCancelConfirm(false)}
              >
                Keep Open
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={handleCancelPoll}
                disabled={actionLoading}
              >
                {actionLoading ? 'Cancelling...' : 'Cancel Poll'}
              </button>
            </div>
          </div>
        </div>
      )}

      {poll.finalized_time && (
        <div className="poll-finalized-banner">
          <h2>Meeting Scheduled</h2>
          <p className="finalized-time">
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

      <div className="poll-results-summary">
        <div className="summary-stat">
          <span className="stat-value">{responseCount}</span>
          <span className="stat-label">Responses</span>
        </div>
        {displayBestSlots.length > 0 && (
          <div className="summary-stat">
            <span className="stat-value">{displayBestSlots[0].available_count}</span>
            <span className="stat-label">Max Available</span>
          </div>
        )}
      </div>

      {displayBestSlots.length > 0 && (
        <div className="poll-best-times">
          <h3>Best Times</h3>
          <ul>
            {displayBestSlots.map((slot, i) => (
              <li key={i} className="best-time-item">
                <span className="best-time-slot">{formatSlot(slot)}</span>
                <span className="best-time-count">
                  {slot.available_count} available
                  {slot.if_needed_count > 0 && ` (+${slot.if_needed_count} if needed)`}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="poll-results-grid">
        <h3>Availability Heatmap</h3>
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

        <div className="poll-results-legend">
          <span className="legend-label">Fewer</span>
          <div className="legend-gradient"></div>
          <span className="legend-label">More Available</span>
        </div>
      </div>

      <div className="poll-respondents-list">
        <h3>Respondents ({responseCount})</h3>
        {responseCount === 0 ? (
          <p className="no-responses">No responses yet. Be the first to add your availability!</p>
        ) : (
          <p className="responses-info">
            {responseCount} {responseCount === 1 ? 'person has' : 'people have'} responded to this poll.
          </p>
        )}
      </div>

      <div className="poll-results-actions">
        {poll.status === 'open' && (
          <Link to={`/ui/polls/respond/${slug}`} className="btn btn-primary">
            Add Your Availability
          </Link>
        )}
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => {
            const url = `${window.location.origin}/ui/polls/respond/${slug}`
            navigator.clipboard.writeText(url)
          }}
        >
          Copy Share Link
        </button>
      </div>
    </div>
  )
}

export default PollResults

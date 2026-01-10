import React, { useState, useEffect, useCallback } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { getPollBySlug, submitPollResponse, getPollResults, getResponseByToken, updatePollResponse } from '../../hooks/usePolls'
import type { Poll, AvailabilitySlot, SlotAggregation, AvailabilityLevel } from '../../hooks/usePolls'
import PollGrid from './PollGrid'
import { getBrowserTimezone, COMMON_TIMEZONES, formatTimezone } from '../../utils/timezones'

export const PollRespond: React.FC = () => {
  const { slug } = useParams<{ slug: string }>()
  const [searchParams] = useSearchParams()
  const editToken = searchParams.get('edit')

  const [poll, setPoll] = useState<Poll | null>(null)
  const [aggregation, setAggregation] = useState<SlotAggregation[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [respondentName, setRespondentName] = useState('')
  const [selectedSlots, setSelectedSlots] = useState<AvailabilitySlot[]>([])
  const [currentLevel, setCurrentLevel] = useState<AvailabilityLevel>(1)
  const [displayTimezone, setDisplayTimezone] = useState(getBrowserTimezone())
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [responseEditToken, setResponseEditToken] = useState<string | null>(null)
  const [existingResponseId, setExistingResponseId] = useState<number | null>(null)
  const [isEditMode, setIsEditMode] = useState(false)

  // Helper to get/set edit token from localStorage
  const getStoredToken = (pollSlug: string): string | null => {
    try {
      return localStorage.getItem(`poll_edit_token_${pollSlug}`)
    } catch {
      return null
    }
  }

  const storeToken = (pollSlug: string, token: string) => {
    try {
      localStorage.setItem(`poll_edit_token_${pollSlug}`, token)
    } catch {
      // localStorage not available, ignore
    }
  }

  // Load poll data
  useEffect(() => {
    if (!slug) return

    const loadPoll = async () => {
      setLoading(true)
      setError(null)
      try {
        const pollData = await getPollBySlug(slug)
        setPoll(pollData)

        // Load results which includes aggregation
        const results = await getPollResults(slug)
        setAggregation(results.aggregated || [])

        // Check for edit token: URL param first, then localStorage
        const tokenToUse = editToken || getStoredToken(slug)

        if (tokenToUse) {
          try {
            const existingResponse = await getResponseByToken(slug, tokenToUse)
            setExistingResponseId(existingResponse.response_id)
            setRespondentName(existingResponse.respondent_name || '')
            setSelectedSlots(existingResponse.availabilities)
            setResponseEditToken(tokenToUse)
            setIsEditMode(true)
            // Ensure token is stored (in case it came from URL)
            storeToken(slug, tokenToUse)
          } catch {
            // Invalid token - clear it and allow new submission
            console.warn('Invalid edit token, ignoring')
            try {
              localStorage.removeItem(`poll_edit_token_${slug}`)
            } catch {
              // ignore
            }
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load poll')
      } finally {
        setLoading(false)
      }
    }

    loadPoll()
  }, [slug, editToken])

  // Handle slot selection changes from grid
  const handleSlotsChange = useCallback((slots: AvailabilitySlot[]) => {
    setSelectedSlots(slots)
  }, [])

  // Submit or update response
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!slug || !poll) return

    setIsSubmitting(true)
    setError(null)

    try {
      if (isEditMode && existingResponseId && responseEditToken) {
        // Update existing response
        await updatePollResponse(slug, existingResponseId, responseEditToken, {
          respondent_name: respondentName.trim(),
          availabilities: selectedSlots,
        })
      } else {
        // Create new response
        const response = await submitPollResponse(slug, {
          respondent_name: respondentName.trim(),
          availabilities: selectedSlots,
        })
        setResponseEditToken(response.edit_token)
        // Store token so returning users auto-edit their response
        storeToken(slug, response.edit_token)
      }

      setSubmitted(true)

      // Refresh results
      const results = await getPollResults(slug)
      setAggregation(results.aggregated || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit response')
    } finally {
      setIsSubmitting(false)
    }
  }

  if (loading) {
    return (
      <div className="poll-respond-page">
        <div className="poll-loading">Loading poll...</div>
      </div>
    )
  }

  if (error && !poll) {
    return (
      <div className="poll-respond-page">
        <div className="poll-error">{error}</div>
      </div>
    )
  }

  if (!poll) {
    return (
      <div className="poll-respond-page">
        <div className="poll-error">Poll not found</div>
      </div>
    )
  }

  if (poll.status !== 'open') {
    return (
      <div className="poll-respond-page">
        <div className="poll-header">
          <h1>{poll.title}</h1>
          {poll.description && <p className="poll-description">{poll.description}</p>}
        </div>
        <div className="poll-closed-message">
          This poll is no longer accepting responses.
          {poll.finalized_time && (
            <p>
              Meeting scheduled for:{' '}
              <strong>
                {new Date(poll.finalized_time).toLocaleString('en-US', {
                  timeZone: displayTimezone,
                  dateStyle: 'full',
                  timeStyle: 'short',
                })}
              </strong>
            </p>
          )}
        </div>
      </div>
    )
  }

  if (submitted) {
    const editUrl = responseEditToken
      ? `${window.location.origin}/ui/polls/respond/${slug}?edit=${responseEditToken}`
      : null

    return (
      <div className="poll-respond-page">
        <div className="poll-header">
          <h1>{poll.title}</h1>
        </div>
        <div className="poll-success">
          <h2>Response submitted!</h2>
          <p>Your availability has been recorded.</p>
          {editUrl && (
            <div className="edit-link-box">
              <p>Save this link to edit your response later:</p>
              <input
                type="text"
                readOnly
                value={editUrl}
                onClick={(e) => (e.target as HTMLInputElement).select()}
              />
              <button
                type="button"
                onClick={() => navigator.clipboard.writeText(editUrl)}
                className="btn btn-secondary"
              >
                Copy Link
              </button>
            </div>
          )}
          <div className="poll-success-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => setSubmitted(false)}
            >
              Edit Response
            </button>
            <a href={`/ui/polls/results/${slug}`} className="btn btn-secondary">
              View Results
            </a>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="poll-respond-page">
      <div className="poll-header">
        <h1>{poll.title}</h1>
        {poll.description && <p className="poll-description">{poll.description}</p>}
      </div>

      {error && <div className="poll-error">{error}</div>}

      <form onSubmit={handleSubmit} className="poll-respond-form">
        <div className="poll-form-row">
          <div className="poll-form-group">
            <label htmlFor="respondentName">Your Name *</label>
            <input
              type="text"
              id="respondentName"
              value={respondentName}
              onChange={(e) => setRespondentName(e.target.value)}
              placeholder="Enter your name"
              required
            />
          </div>

          <div className="poll-form-group">
            <label htmlFor="displayTimezone">Your Timezone</label>
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
        </div>

        <div className="poll-level-selector">
          <label>Availability Level:</label>
          <div className="level-buttons">
            <button
              type="button"
              className={`level-btn ${currentLevel === 1 ? 'active available' : ''}`}
              onClick={() => setCurrentLevel(1)}
            >
              Available
            </button>
            <button
              type="button"
              className={`level-btn ${currentLevel === 2 ? 'active if-needed' : ''}`}
              onClick={() => setCurrentLevel(2)}
            >
              If Needed
            </button>
          </div>
          <p className="level-hint">
            Click and drag on the grid to mark your availability
          </p>
        </div>

        <div className="poll-grid-wrapper">
          <PollGrid
            datetimeStart={new Date(poll.datetime_start)}
            datetimeEnd={new Date(poll.datetime_end)}
            slotDurationMinutes={poll.slot_duration_minutes}
            displayTimezone={displayTimezone}
            selectedSlots={selectedSlots}
            onSlotsChange={handleSlotsChange}
            availabilityLevel={currentLevel}
          />
        </div>

        <div className="poll-respond-legend">
          <div className="legend-item">
            <span className="legend-color available"></span>
            <span>Available</span>
          </div>
          <div className="legend-item">
            <span className="legend-color if-needed"></span>
            <span>If Needed</span>
          </div>
        </div>

        <div className="poll-form-actions">
          <button
            type="submit"
            className="btn btn-primary"
            disabled={isSubmitting || !respondentName.trim()}
          >
            {isSubmitting ? 'Submitting...' : isEditMode ? 'Update Availability' : 'Submit Availability'}
          </button>
        </div>
      </form>

      {aggregation.length > 0 && (
        <div className="poll-current-results">
          <h3>Current Responses ({poll.response_count})</h3>
          <p className="results-hint">
            <a href={`/ui/polls/results/${slug}`}>View full results</a>
          </p>
        </div>
      )}
    </div>
  )
}

export default PollRespond

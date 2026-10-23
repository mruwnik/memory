import React, { useState, useEffect, useCallback } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { getPollBySlug, submitPollResponse, getPollResults, getResponseByToken, updatePollResponse } from '../../hooks/usePolls'
import type { Poll, AvailabilitySlot, SlotAggregation, AvailabilityLevel } from '../../hooks/usePolls'
import PollGrid from './PollGrid'
import { getBrowserTimezone, COMMON_TIMEZONES, formatTimezone } from '../../utils/timezones'
import { Button, ButtonLink, Card, Input, Label, Notice, Page, Select } from '@/components/shared/ui'

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

  if (loading || !poll) {
    return <Page title="Availability poll" width="narrow" loading={loading && 'Loading poll...'} error={loading ? null : error ?? 'Poll not found'} />
  }

  if (poll.status !== 'open') {
    return (
      <Page title={poll.title} description={poll.description} width="narrow">
          <Notice tone="warning">
            <p className="mb-2">This poll is no longer accepting responses.</p>
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
          </Notice>
      </Page>
    )
  }

  if (submitted) {
    const editUrl = responseEditToken
      ? `${window.location.origin}/ui/polls/respond/${slug}?edit=${responseEditToken}`
      : null

    return (
      <Page title={poll.title} width="narrow">
          <div className="bg-green-50 border border-green-200 rounded-xl p-6 text-center">
            <h2 className="text-xl font-semibold text-green-800 mb-2">Response submitted!</h2>
            <p className="text-green-700 mb-4">Your availability has been recorded.</p>
            {editUrl && (
              <div className="bg-white border border-slate-200 rounded-lg p-4 mb-4 text-left">
                <p className="text-sm text-slate-600 mb-2">Save this link to edit your response later:</p>
                <div className="flex gap-2">
                  <Input
                    type="text"
                    readOnly
                    value={editUrl}
                    onClick={(e) => (e.target as HTMLInputElement).select()}
                    className="flex-1 bg-slate-50"
                  />
                  <Button
                    type="button"
                    onClick={() => navigator.clipboard.writeText(editUrl)}
                  >
                    Copy
                  </Button>
                </div>
              </div>
            )}
            <div className="flex justify-center gap-3">
              <Button variant="primary"
                type="button"
                onClick={() => setSubmitted(false)}
              >
                Edit Response
              </Button>
              <ButtonLink to={`/ui/polls/results/${slug}`}>View Results</ButtonLink>
            </div>
          </div>
      </Page>
    )
  }

  return (
    <Page title={poll.title} description={poll.description} width="narrow">
        {error && (
          <Notice tone="error">{error}</Notice>
        )}

        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Name and Timezone */}
          <Card>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <Label htmlFor="respondentName" className="mb-1">
                  Your Name *
                </Label>
                <Input
                  type="text"
                  id="respondentName"
                  value={respondentName}
                  onChange={(e) => setRespondentName(e.target.value)}
                  placeholder="Enter your name"
                  required
                />
              </div>

              <div>
                <Label htmlFor="displayTimezone" className="mb-1">
                  Your Timezone
                </Label>
                <Select
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
                </Select>
              </div>
            </div>
          </Card>

          {/* Availability Level Selector */}
          <Card>
            <Label className="mb-2">Availability Level:</Label>
            <div className="flex gap-2 mb-2">
              <button
                type="button"
                className={`py-2 px-4 rounded-lg text-sm font-medium transition-colors ${
                  currentLevel === 1
                    ? 'bg-green-500 text-white'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                }`}
                onClick={() => setCurrentLevel(1)}
              >
                Available
              </button>
              <button
                type="button"
                className={`py-2 px-4 rounded-lg text-sm font-medium transition-colors ${
                  currentLevel === 2
                    ? 'bg-yellow-400 text-slate-800'
                    : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                }`}
                onClick={() => setCurrentLevel(2)}
              >
                If Needed
              </button>
            </div>
            <p className="text-sm text-slate-500">
              Click and drag on the grid to mark your availability
            </p>
          </Card>

          {/* Grid */}
          <Card className="overflow-x-auto">
            <PollGrid
              datetimeStart={new Date(poll.datetime_start)}
              datetimeEnd={new Date(poll.datetime_end)}
              slotDurationMinutes={poll.slot_duration_minutes}
              displayTimezone={displayTimezone}
              selectedSlots={selectedSlots}
              onSlotsChange={handleSlotsChange}
              availabilityLevel={currentLevel}
            />
          </Card>

          {/* Legend */}
          <div className="flex justify-center gap-6 text-sm">
            <div className="flex items-center gap-2">
              <span className="w-4 h-4 rounded bg-green-500" />
              <span className="text-slate-600">Available</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-4 h-4 rounded bg-yellow-400" />
              <span className="text-slate-600">If Needed</span>
            </div>
          </div>

          {/* Submit */}
          <div className="flex justify-center">
            <Button variant="primary"
              type="submit"
              disabled={isSubmitting || !respondentName.trim()}
            >
              {isSubmitting ? 'Submitting...' : isEditMode ? 'Update Availability' : 'Submit Availability'}
            </Button>
          </div>
        </form>

        {/* Current Results Link */}
        {aggregation.length > 0 && (
          <div className="text-center">
            <p className="text-slate-600">
              {poll.response_count} {poll.response_count === 1 ? 'response' : 'responses'} so far.{' '}
              <a href={`/ui/polls/results/${slug}`} className="text-primary hover:underline">
                View full results
              </a>
            </p>
          </div>
        )}
    </Page>
  )
}

export default PollRespond

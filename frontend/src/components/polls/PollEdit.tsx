import React, { useState, useEffect } from 'react'
import { useNavigate, useParams, Link } from 'react-router-dom'
import { usePolls, getPollBySlug } from '../../hooks/usePolls'
import type { Poll, PollStatus } from '../../hooks/usePolls'
import {
  formatHour,
  getBrowserTimezone,
  COMMON_TIMEZONES,
  formatTimezone,
  toUTCDatetime,
  fromUTCToLocal,
} from '../../utils/timezones'

interface FormData {
  title: string
  description: string
  status: PollStatus
  timezone: string
  date_start: string
  date_end: string
  time_start: number
  time_end: number
  slot_duration: 15 | 30 | 60
}

export const PollEdit: React.FC = () => {
  const { slug } = useParams<{ slug: string }>()
  const navigate = useNavigate()
  const { updatePoll } = usePolls()

  const [poll, setPoll] = useState<Poll | null>(null)
  const [loading, setLoading] = useState(true)
  const [formData, setFormData] = useState<FormData | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Load existing poll data
  useEffect(() => {
    if (!slug) return

    const loadPoll = async () => {
      setLoading(true)
      setError(null)
      try {
        const pollData = await getPollBySlug(slug)
        setPoll(pollData)

        // Convert UTC datetimes to local form values
        const timezone = getBrowserTimezone()
        const start = fromUTCToLocal(pollData.datetime_start, timezone)
        const end = fromUTCToLocal(pollData.datetime_end, timezone)

        setFormData({
          title: pollData.title,
          description: pollData.description || '',
          status: pollData.status,
          timezone,
          date_start: start.date,
          date_end: end.date,
          time_start: start.hour,
          time_end: end.hour === 0 ? 24 : end.hour, // Handle midnight as 24
          slot_duration: pollData.slot_duration_minutes as 15 | 30 | 60,
        })
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load poll')
      } finally {
        setLoading(false)
      }
    }

    loadPoll()
  }, [slug])

  const handleChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>
  ) => {
    if (!formData) return
    const { name, value, type } = e.target
    setFormData(prev => prev ? ({
      ...prev,
      [name]: type === 'number' || name === 'time_start' || name === 'time_end' || name === 'slot_duration'
        ? parseInt(value, 10)
        : value,
    }) : null)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!formData || !poll) return

    setError(null)
    setIsSubmitting(true)

    try {
      // Convert local date/time to UTC datetimes
      const datetime_start = toUTCDatetime(formData.date_start, formData.time_start, formData.timezone)
      const datetime_end = toUTCDatetime(formData.date_end, formData.time_end, formData.timezone)

      await updatePoll({
        poll_id: poll.id,
        title: formData.title,
        description: formData.description || undefined,
        status: formData.status,
        datetime_start,
        datetime_end,
        slot_duration: formData.slot_duration,
      })
      navigate(`/ui/polls/results/${slug}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update poll')
    } finally {
      setIsSubmitting(false)
    }
  }

  if (loading) {
    return (
      <div className="polls-page">
        <div className="poll-loading">Loading poll...</div>
      </div>
    )
  }

  if (error && !formData) {
    return (
      <div className="polls-page">
        <div className="poll-error">{error}</div>
        <Link to="/ui/polls" className="btn btn-secondary">Back to Polls</Link>
      </div>
    )
  }

  if (!formData || !poll) {
    return (
      <div className="polls-page">
        <div className="poll-error">Poll not found</div>
        <Link to="/ui/polls" className="btn btn-secondary">Back to Polls</Link>
      </div>
    )
  }

  return (
    <div className="polls-page">
      <form className="poll-form" onSubmit={handleSubmit}>
        <h2>Edit Poll</h2>

        {error && (
          <div className="error-message" style={{ marginBottom: '1rem', color: '#dc2626' }}>
            {error}
          </div>
        )}

        <div className="poll-form-group">
          <label htmlFor="title">Poll Title *</label>
          <input
            type="text"
            id="title"
            name="title"
            value={formData.title}
            onChange={handleChange}
            placeholder="e.g., Team Planning Meeting"
            required
          />
        </div>

        <div className="poll-form-group">
          <label htmlFor="description">Description</label>
          <textarea
            id="description"
            name="description"
            value={formData.description || ''}
            onChange={handleChange}
            placeholder="Optional description or agenda"
            rows={3}
          />
        </div>

        <div className="poll-form-group">
          <label htmlFor="status">Status</label>
          <select
            id="status"
            name="status"
            value={formData.status}
            onChange={handleChange}
          >
            <option value="open">Open</option>
            <option value="closed">Closed</option>
            <option value="finalized">Finalized</option>
          </select>
          <small style={{ color: 'var(--text-secondary)', marginTop: '0.25rem', display: 'block' }}>
            {formData.status === 'open' && 'Respondents can submit their availability'}
            {formData.status === 'closed' && 'No new responses allowed, but not finalized yet'}
            {formData.status === 'finalized' && 'Poll is complete with a scheduled time'}
          </small>
        </div>

        <div className="poll-form-row">
          <div className="poll-form-group">
            <label htmlFor="date_start">Start Date *</label>
            <input
              type="date"
              id="date_start"
              name="date_start"
              value={formData.date_start}
              onChange={handleChange}
              required
                          />
          </div>

          <div className="poll-form-group">
            <label htmlFor="date_end">End Date *</label>
            <input
              type="date"
              id="date_end"
              name="date_end"
              value={formData.date_end}
              onChange={handleChange}
              required
                          />
          </div>
        </div>

        <div className="poll-form-row">
          <div className="poll-form-group">
            <label htmlFor="time_start">Start Hour</label>
            <select
              id="time_start"
              name="time_start"
              value={formData.time_start}
              onChange={handleChange}
                          >
              {Array.from({ length: 24 }, (_, i) => (
                <option key={i} value={i}>{formatHour(i)}</option>
              ))}
            </select>
          </div>

          <div className="poll-form-group">
            <label htmlFor="time_end">End Hour</label>
            <select
              id="time_end"
              name="time_end"
              value={formData.time_end}
              onChange={handleChange}
                          >
              {Array.from({ length: 24 }, (_, i) => (
                <option key={i + 1} value={i + 1}>{formatHour(i + 1)}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="poll-form-row">
          <div className="poll-form-group">
            <label htmlFor="slot_duration">Time Slot Duration</label>
            <select
              id="slot_duration"
              name="slot_duration"
              value={formData.slot_duration}
              onChange={handleChange}
              disabled={poll.response_count > 0}
            >
              <option value={15}>15 minutes</option>
              <option value={30}>30 minutes</option>
              <option value={60}>1 hour</option>
            </select>
            {poll.response_count > 0 && (
              <small style={{ color: 'var(--text-secondary)', marginTop: '0.25rem', display: 'block' }}>
                Cannot change slot duration when responses exist
              </small>
            )}
          </div>

          <div className="poll-form-group">
            <label htmlFor="timezone">Display Timezone</label>
            <select
              id="timezone"
              name="timezone"
              value={formData.timezone}
              onChange={handleChange}
            >
              {!COMMON_TIMEZONES.includes(formData.timezone) && (
                <option value={formData.timezone}>{formatTimezone(formData.timezone)}</option>
              )}
              {COMMON_TIMEZONES.map(tz => (
                <option key={tz} value={tz}>{formatTimezone(tz)}</option>
              ))}
            </select>
          </div>
        </div>

        <p className="poll-form-hint" style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginBottom: '1rem' }}>
          Times will be converted to UTC and displayed in each respondent's local timezone.
        </p>

        <div className="poll-form-actions">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => navigate(`/ui/polls/results/${slug}`)}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={isSubmitting || !formData.title}
          >
            {isSubmitting ? 'Saving...' : 'Save Changes'}
          </button>
        </div>
      </form>
    </div>
  )
}

export default PollEdit

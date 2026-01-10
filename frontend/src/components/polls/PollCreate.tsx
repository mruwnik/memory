import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { usePolls } from '../../hooks/usePolls'
import {
  formatHour,
  getBrowserTimezone,
  COMMON_TIMEZONES,
  formatTimezone,
  toUTCDatetime,
} from '../../utils/timezones'

// Get tomorrow's date as default start
function getDefaultStartDate(): string {
  const tomorrow = new Date()
  tomorrow.setDate(tomorrow.getDate() + 1)
  return tomorrow.toISOString().split('T')[0]
}

// Get a week from tomorrow as default end
function getDefaultEndDate(): string {
  const nextWeek = new Date()
  nextWeek.setDate(nextWeek.getDate() + 8)
  return nextWeek.toISOString().split('T')[0]
}

interface FormData {
  title: string
  description: string
  timezone: string
  date_start: string
  date_end: string
  time_start: number
  time_end: number
  slot_duration: 15 | 30 | 60
}

export const PollCreate: React.FC = () => {
  const navigate = useNavigate()
  const { createPoll } = usePolls()

  const [formData, setFormData] = useState<FormData>({
    title: '',
    description: '',
    timezone: getBrowserTimezone(),
    date_start: getDefaultStartDate(),
    date_end: getDefaultEndDate(),
    time_start: 9,
    time_end: 17,
    slot_duration: 30,
  })

  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>
  ) => {
    const { name, value, type } = e.target
    setFormData(prev => ({
      ...prev,
      [name]: type === 'number' || name === 'time_start' || name === 'time_end' || name === 'slot_duration'
        ? parseInt(value, 10) 
        : value,
    }))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setIsSubmitting(true)

    try {
      // Convert local date/time to UTC datetimes
      const datetime_start = toUTCDatetime(formData.date_start, formData.time_start, formData.timezone)
      const datetime_end = toUTCDatetime(formData.date_end, formData.time_end, formData.timezone)
      
      const poll = await createPoll({
        title: formData.title,
        description: formData.description || undefined,
        datetime_start,
        datetime_end,
        slot_duration: formData.slot_duration,
      })
      navigate(`/ui/polls/results/${poll.slug}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create poll')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="polls-page">
      <form className="poll-form" onSubmit={handleSubmit}>
        <h2>Create Availability Poll</h2>

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
            >
              <option value={15}>15 minutes</option>
              <option value={30}>30 minutes</option>
              <option value={60}>1 hour</option>
            </select>
          </div>

          <div className="poll-form-group">
            <label htmlFor="timezone">Your Timezone</label>
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
            onClick={() => navigate('/ui/polls')}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={isSubmitting || !formData.title}
          >
            {isSubmitting ? 'Creating...' : 'Create Poll'}
          </button>
        </div>
      </form>
    </div>
  )
}

export default PollCreate

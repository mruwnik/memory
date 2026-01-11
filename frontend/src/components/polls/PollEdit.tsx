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
      <div className="min-h-screen bg-slate-50 p-4 md:p-8">
        <div className="text-center py-8 text-slate-500">Loading poll...</div>
      </div>
    )
  }

  if (error && !formData) {
    return (
      <div className="min-h-screen bg-slate-50 p-4 md:p-8">
        <div className="max-w-xl mx-auto">
          <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg mb-4">
            {error}
          </div>
          <Link to="/ui/polls" className="py-2 px-4 bg-slate-100 text-slate-700 rounded-lg hover:bg-slate-200">
            Back to Polls
          </Link>
        </div>
      </div>
    )
  }

  if (!formData || !poll) {
    return (
      <div className="min-h-screen bg-slate-50 p-4 md:p-8">
        <div className="max-w-xl mx-auto">
          <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg mb-4">
            Poll not found
          </div>
          <Link to="/ui/polls" className="py-2 px-4 bg-slate-100 text-slate-700 rounded-lg hover:bg-slate-200">
            Back to Polls
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-50 p-4 md:p-8">
      <form className="max-w-xl mx-auto bg-white p-6 rounded-xl shadow-md" onSubmit={handleSubmit}>
        <h2 className="text-xl font-semibold text-slate-800 mb-6">Edit Poll</h2>

        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
            {error}
          </div>
        )}

        <div className="mb-4">
          <label htmlFor="title" className="block text-sm font-medium text-slate-700 mb-1">
            Poll Title *
          </label>
          <input
            type="text"
            id="title"
            name="title"
            value={formData.title}
            onChange={handleChange}
            placeholder="e.g., Team Planning Meeting"
            required
            className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
        </div>

        <div className="mb-4">
          <label htmlFor="description" className="block text-sm font-medium text-slate-700 mb-1">
            Description
          </label>
          <textarea
            id="description"
            name="description"
            value={formData.description || ''}
            onChange={handleChange}
            placeholder="Optional description or agenda"
            rows={3}
            className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 resize-none"
          />
        </div>

        <div className="mb-4">
          <label htmlFor="status" className="block text-sm font-medium text-slate-700 mb-1">
            Status
          </label>
          <select
            id="status"
            name="status"
            value={formData.status}
            onChange={handleChange}
            className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm bg-white focus:border-primary focus:outline-none"
          >
            <option value="open">Open</option>
            <option value="closed">Closed</option>
            <option value="finalized">Finalized</option>
          </select>
          <p className="mt-1 text-xs text-slate-500">
            {formData.status === 'open' && 'Respondents can submit their availability'}
            {formData.status === 'closed' && 'No new responses allowed, but not finalized yet'}
            {formData.status === 'finalized' && 'Poll is complete with a scheduled time'}
          </p>
        </div>

        <div className="grid grid-cols-2 gap-4 mb-4">
          <div>
            <label htmlFor="date_start" className="block text-sm font-medium text-slate-700 mb-1">
              Start Date *
            </label>
            <input
              type="date"
              id="date_start"
              name="date_start"
              value={formData.date_start}
              onChange={handleChange}
              required
              className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none"
            />
          </div>

          <div>
            <label htmlFor="date_end" className="block text-sm font-medium text-slate-700 mb-1">
              End Date *
            </label>
            <input
              type="date"
              id="date_end"
              name="date_end"
              value={formData.date_end}
              onChange={handleChange}
              required
              className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm focus:border-primary focus:outline-none"
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4 mb-4">
          <div>
            <label htmlFor="time_start" className="block text-sm font-medium text-slate-700 mb-1">
              Start Hour
            </label>
            <select
              id="time_start"
              name="time_start"
              value={formData.time_start}
              onChange={handleChange}
              className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm bg-white focus:border-primary focus:outline-none"
            >
              {Array.from({ length: 24 }, (_, i) => (
                <option key={i} value={i}>{formatHour(i)}</option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="time_end" className="block text-sm font-medium text-slate-700 mb-1">
              End Hour
            </label>
            <select
              id="time_end"
              name="time_end"
              value={formData.time_end}
              onChange={handleChange}
              className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm bg-white focus:border-primary focus:outline-none"
            >
              {Array.from({ length: 24 }, (_, i) => (
                <option key={i + 1} value={i + 1}>{formatHour(i + 1)}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4 mb-4">
          <div>
            <label htmlFor="slot_duration" className="block text-sm font-medium text-slate-700 mb-1">
              Time Slot Duration
            </label>
            <select
              id="slot_duration"
              name="slot_duration"
              value={formData.slot_duration}
              onChange={handleChange}
              disabled={poll.response_count > 0}
              className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm bg-white focus:border-primary focus:outline-none disabled:bg-slate-50 disabled:text-slate-400"
            >
              <option value={15}>15 minutes</option>
              <option value={30}>30 minutes</option>
              <option value={60}>1 hour</option>
            </select>
            {poll.response_count > 0 && (
              <p className="mt-1 text-xs text-slate-500">
                Cannot change slot duration when responses exist
              </p>
            )}
          </div>

          <div>
            <label htmlFor="timezone" className="block text-sm font-medium text-slate-700 mb-1">
              Display Timezone
            </label>
            <select
              id="timezone"
              name="timezone"
              value={formData.timezone}
              onChange={handleChange}
              className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm bg-white focus:border-primary focus:outline-none"
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

        <p className="text-sm text-slate-500 mb-6">
          Times will be converted to UTC and displayed in each respondent's local timezone.
        </p>

        <div className="flex justify-end gap-3">
          <button
            type="button"
            className="py-2 px-4 border border-slate-200 rounded-lg text-slate-600 hover:bg-slate-50"
            onClick={() => navigate(`/ui/polls/results/${slug}`)}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="py-2 px-4 bg-primary text-white rounded-lg font-medium hover:bg-primary-dark disabled:bg-slate-300 disabled:cursor-not-allowed"
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

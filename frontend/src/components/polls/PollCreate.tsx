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
    <div className="min-h-screen bg-slate-50 p-4 md:p-8">
      <form className="max-w-xl mx-auto bg-white p-6 rounded-xl shadow-md" onSubmit={handleSubmit}>
        <h2 className="text-xl font-semibold text-slate-800 mb-6">Create Availability Poll</h2>

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
              className="w-full py-2 px-3 border border-slate-200 rounded-lg text-sm bg-white focus:border-primary focus:outline-none"
            >
              <option value={15}>15 minutes</option>
              <option value={30}>30 minutes</option>
              <option value={60}>1 hour</option>
            </select>
          </div>

          <div>
            <label htmlFor="timezone" className="block text-sm font-medium text-slate-700 mb-1">
              Your Timezone
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
            onClick={() => navigate('/ui/polls')}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="py-2 px-4 bg-primary text-white rounded-lg font-medium hover:bg-primary-dark disabled:bg-slate-300 disabled:cursor-not-allowed"
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

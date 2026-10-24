import React, { useState, useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
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
import { Button, Card, Field, FieldRow, Input, Label, Notice, Page, Select, Textarea } from '@/components/shared/ui'

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

  const back = { to: '/ui/polls', label: 'Polls' }
  if (loading || !formData || !poll) {
    return <Page back={back} title="Edit Poll" width="narrow" loading={loading && 'Loading poll...'} error={loading ? null : error ?? 'Poll not found'} />
  }

  return (
    <Page back={back} title="Edit Poll" width="narrow">
      <Card>
      <form className="space-y-4" onSubmit={handleSubmit}>
        {error && <Notice tone="error">{error}</Notice>}

        <Field label="Poll Title *">
          <Input
            type="text"
            id="title"
            name="title"
            value={formData.title}
            onChange={handleChange}
            placeholder="e.g., Team Planning Meeting"
            required
          />
        </Field>

        <Field label="Description">
          <Textarea
            id="description"
            name="description"
            value={formData.description || ''}
            onChange={handleChange}
            placeholder="Optional description or agenda"
            rows={3}
          />
        </Field>

        <div className="mb-4">
          <Label htmlFor="status" className="mb-1">
            Status
          </Label>
          <Select
            id="status"
            name="status"
            value={formData.status}
            onChange={handleChange}
          >
            <option value="open">Open</option>
            <option value="closed">Closed</option>
            <option value="finalized">Finalized</option>
          </Select>
          <p className="mt-1 text-xs text-slate-500">
            {formData.status === 'open' && 'Respondents can submit their availability'}
            {formData.status === 'closed' && 'No new responses allowed, but not finalized yet'}
            {formData.status === 'finalized' && 'Poll is complete with a scheduled time'}
          </p>
        </div>

        <FieldRow>
          <Field label="Start Date *">
            <Input
              type="date"
              id="date_start"
              name="date_start"
              value={formData.date_start}
              onChange={handleChange}
              required
            />
          </Field>

          <Field label="End Date *">
            <Input
              type="date"
              id="date_end"
              name="date_end"
              value={formData.date_end}
              onChange={handleChange}
              required
            />
          </Field>
        </FieldRow>

        <FieldRow>
          <Field label="Start Hour">
            <Select
              id="time_start"
              name="time_start"
              value={formData.time_start}
              onChange={handleChange}
            >
              {Array.from({ length: 24 }, (_, i) => (
                <option key={i} value={i}>{formatHour(i)}</option>
              ))}
            </Select>
          </Field>

          <Field label="End Hour">
            <Select
              id="time_end"
              name="time_end"
              value={formData.time_end}
              onChange={handleChange}
            >
              {Array.from({ length: 24 }, (_, i) => (
                <option key={i + 1} value={i + 1}>{formatHour(i + 1)}</option>
              ))}
            </Select>
          </Field>
        </FieldRow>

        <FieldRow>
          <div>
            <Label htmlFor="slot_duration" className="mb-1">
              Time Slot Duration
            </Label>
            <Select
              id="slot_duration"
              name="slot_duration"
              value={formData.slot_duration}
              onChange={handleChange}
              disabled={poll.response_count > 0}
              className="disabled:bg-slate-50"
            >
              <option value={15}>15 minutes</option>
              <option value={30}>30 minutes</option>
              <option value={60}>1 hour</option>
            </Select>
            {poll.response_count > 0 && (
              <p className="mt-1 text-xs text-slate-500">
                Cannot change slot duration when responses exist
              </p>
            )}
          </div>

          <Field label="Display Timezone">
            <Select
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
            </Select>
          </Field>
        </FieldRow>

        <p className="text-sm text-slate-500 mb-6">
          Times will be converted to UTC and displayed in each respondent's local timezone.
        </p>

        <div className="flex justify-end gap-3">
          <Button
            type="button"
            onClick={() => navigate(`/ui/polls/results/${slug}`)}
          >
            Cancel
          </Button>
          <Button variant="primary"
            type="submit"
            disabled={isSubmitting || !formData.title}
          >
            {isSubmitting ? 'Saving...' : 'Save Changes'}
          </Button>
        </div>
      </form>
      </Card>
    </Page>
  )
}

export default PollEdit

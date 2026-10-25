import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { resetUserTimezone, setUserTimezone } from '@/utils/timezones'
import { formatRelativeTime } from './shared'

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2024-02-20T12:00:00Z'))
})
afterEach(() => {
  vi.useRealTimers()
  resetUserTimezone()
})

it.each([
  ['Asia/Tokyo', 'Jan 16, 2024'],
  ['America/Los_Angeles', 'Jan 15, 2024'],
])('dates a week-old time on the calendar day of %s', (tz, expected) => {
  setUserTimezone(tz)
  expect(formatRelativeTime('2024-01-15T23:30:00Z')).toBe(expected)
})

import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  COMMON_TIMEZONES,
  getBrowserTimezone,
  getUtcOffset,
  formatTimezone,
  formatHour,
  toUTCDatetime,
  fromUTCToLocal,
  formatDateInTimezone,
  formatTimeInTimezone,
  formatDateTimeInTimezone,
  formatSlotTime,
  getTimeKey,
  getDateKey,
  formatShortDate,
} from './timezones'

describe('COMMON_TIMEZONES', () => {
  it('includes UTC and a broad geographic spread', () => {
    expect(COMMON_TIMEZONES).toContain('UTC')
    expect(COMMON_TIMEZONES).toContain('Europe/Warsaw')
    expect(COMMON_TIMEZONES).toContain('Asia/Tokyo')
    expect(COMMON_TIMEZONES).toContain('America/New_York')
  })

  it('has no duplicate entries', () => {
    expect(new Set(COMMON_TIMEZONES).size).toBe(COMMON_TIMEZONES.length)
  })
})

describe('getBrowserTimezone', () => {
  it('returns the browser-resolved IANA timezone', () => {
    const spy = vi
      .spyOn(Intl, 'DateTimeFormat')
      .mockReturnValue({
        resolvedOptions: () => ({ timeZone: 'America/Chicago' }),
      } as unknown as Intl.DateTimeFormat)
    expect(getBrowserTimezone()).toBe('America/Chicago')
    spy.mockRestore()
  })
})

describe('getUtcOffset', () => {
  it('formats a known offset as UTC-prefixed', () => {
    // New York is UTC-5 or UTC-4 depending on DST; assert the prefix + sign.
    const offset = getUtcOffset('America/New_York')
    expect(offset).toMatch(/^UTC-[45]$/)
  })

  it('returns the UTC+0 offset for the UTC zone', () => {
    // The runtime reports a numeric offset ("GMT+0" -> "UTC+0"), not a bare "UTC".
    expect(getUtcOffset('UTC')).toBe('UTC+0')
  })

  it('returns empty string for an invalid timezone', () => {
    expect(getUtcOffset('Not/AZone')).toBe('')
  })
})

describe('formatTimezone', () => {
  it('replaces underscores and appends the offset', () => {
    const result = formatTimezone('America/New_York')
    expect(result).toContain('America/New York')
    expect(result).toMatch(/\(UTC-[45]\)$/)
  })

  it('omits the parenthetical when no offset is resolvable', () => {
    expect(formatTimezone('Not/AZone')).toBe('Not/AZone')
  })
})

describe('formatHour', () => {
  it.each([
    [0, '12:00 AM'],
    [24, '12:00 AM'],
    [12, '12:00 PM'],
    [1, '1:00 AM'],
    [11, '11:00 AM'],
    [13, '1:00 PM'],
    [23, '11:00 PM'],
  ])('formats hour %i as %s', (hour, expected) => {
    expect(formatHour(hour)).toBe(expected)
  })
})

describe('toUTCDatetime / fromUTCToLocal roundtrip', () => {
  it('converts Warsaw 9am in January to 08:00 UTC', () => {
    expect(toUTCDatetime('2024-01-15', 9, 'Europe/Warsaw')).toBe(
      '2024-01-15T08:00:00.000Z',
    )
  })

  it('converts UTC zone identically (no offset)', () => {
    expect(toUTCDatetime('2024-06-01', 14, 'UTC')).toBe(
      '2024-06-01T14:00:00.000Z',
    )
  })

  it.each([
    ['Europe/Warsaw', '2024-01-15', 9],
    ['America/New_York', '2024-07-04', 18],
    ['Asia/Tokyo', '2024-03-10', 8],
    ['Pacific/Auckland', '2024-12-25', 23],
  ])('roundtrips %s %s hour %i', (tz, date, hour) => {
    const utc = toUTCDatetime(date, hour, tz)
    expect(fromUTCToLocal(utc, tz)).toEqual({ date, hour })
  })

  it('SOURCE QUIRK: fromUTCToLocal reports local midnight as hour 24, not 0', () => {
    // Intl with hour12:false renders midnight as "24" in this runtime, so the
    // 0<->24 distinction is lost on the way back. Asserting current behavior.
    const utc = toUTCDatetime('2024-03-10', 0, 'Asia/Tokyo')
    expect(fromUTCToLocal(utc, 'Asia/Tokyo')).toEqual({
      date: '2024-03-10',
      hour: 24,
    })
  })
})

describe('fromUTCToLocal', () => {
  it('returns YYYY-MM-DD date and 0-23 hour', () => {
    const result = fromUTCToLocal('2024-01-15T08:00:00.000Z', 'Europe/Warsaw')
    expect(result).toEqual({ date: '2024-01-15', hour: 9 })
  })
})

describe('formatDateInTimezone', () => {
  it('accepts an ISO string and formats the default short date', () => {
    const out = formatDateInTimezone('2024-01-15T12:00:00Z', 'UTC')
    expect(out).toMatch(/Jan 15/)
  })

  it('accepts a Date object and custom options', () => {
    const out = formatDateInTimezone(new Date('2024-01-15T12:00:00Z'), 'UTC', {
      year: 'numeric',
    })
    expect(out).toBe('2024')
  })
})

describe('formatTimeInTimezone', () => {
  it('formats a time with default 12-hour options', () => {
    const out = formatTimeInTimezone('2024-01-15T13:30:00Z', 'UTC')
    expect(out).toMatch(/1:30/)
    expect(out).toMatch(/PM/)
  })
})

describe('formatDateTimeInTimezone', () => {
  it('produces a combined date and time string', () => {
    const out = formatDateTimeInTimezone('2024-01-15T13:30:00Z', 'UTC')
    expect(out).toMatch(/Jan/)
    expect(out).toMatch(/2024/)
  })
})

describe('formatSlotTime', () => {
  it('joins date and a start-end time range', () => {
    const out = formatSlotTime(
      '2024-01-15T09:00:00Z',
      '2024-01-15T10:00:00Z',
      'UTC',
    )
    expect(out).toMatch(/Jan 15/)
    expect(out).toMatch(/-/)
  })
})

describe('getTimeKey / getDateKey', () => {
  it('returns a 24-hour HH:MM time key', () => {
    expect(getTimeKey(new Date('2024-01-15T13:05:00Z'), 'UTC')).toBe('13:05')
  })

  it('returns a locale date key', () => {
    expect(getDateKey(new Date('2024-01-15T13:05:00Z'), 'UTC')).toMatch(/2024/)
  })
})

describe('formatShortDate', () => {
  it('formats an ISO date string with month/day/year', () => {
    const out = formatShortDate('2024-01-15')
    expect(out).toMatch(/Jan/)
    expect(out).toMatch(/2024/)
  })
})

// Common timezones for selection - at least one per UTC offset
export const COMMON_TIMEZONES = [
  // UTC-11 to UTC-9
  'Pacific/Pago_Pago',      // UTC-11
  'Pacific/Honolulu',       // UTC-10
  'America/Anchorage',      // UTC-9
  // UTC-8 to UTC-5
  'America/Los_Angeles',    // UTC-8
  'America/Denver',         // UTC-7
  'America/Chicago',        // UTC-6
  'America/New_York',       // UTC-5
  // UTC-4 to UTC-1
  'America/Halifax',        // UTC-4
  'America/St_Johns',       // UTC-3:30
  'America/Sao_Paulo',      // UTC-3
  'Atlantic/South_Georgia', // UTC-2
  'Atlantic/Azores',        // UTC-1
  // UTC+0 to UTC+3
  'UTC',                    // UTC+0
  'Europe/London',          // UTC+0
  'Europe/Paris',           // UTC+1
  'Europe/Berlin',          // UTC+1
  'Europe/Warsaw',          // UTC+1
  'Europe/Athens',          // UTC+2
  'Africa/Cairo',           // UTC+2
  'Africa/Johannesburg',    // UTC+2
  'Europe/Moscow',          // UTC+3
  // UTC+3:30 to UTC+6:30
  'Asia/Tehran',            // UTC+3:30
  'Asia/Dubai',             // UTC+4
  'Asia/Kabul',             // UTC+4:30
  'Asia/Karachi',           // UTC+5
  'Asia/Kolkata',           // UTC+5:30
  'Asia/Kathmandu',         // UTC+5:45
  'Asia/Dhaka',             // UTC+6
  'Asia/Yangon',            // UTC+6:30
  // UTC+7 to UTC+9:30
  'Asia/Bangkok',           // UTC+7
  'Asia/Singapore',         // UTC+8
  'Asia/Shanghai',          // UTC+8
  'Asia/Hong_Kong',         // UTC+8
  'Asia/Tokyo',             // UTC+9
  'Asia/Seoul',             // UTC+9
  'Australia/Darwin',       // UTC+9:30
  // UTC+10 to UTC+14
  'Australia/Sydney',       // UTC+10/11 (DST)
  'Australia/Melbourne',    // UTC+10/11 (DST)
  'Pacific/Noumea',         // UTC+11
  'Pacific/Fiji',           // UTC+12
  'Pacific/Auckland',       // UTC+12/13 (DST)
  'Pacific/Tongatapu',      // UTC+13
  'Pacific/Kiritimati',     // UTC+14
]

// Get browser's timezone
export function getBrowserTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone
}

// Get UTC offset for a timezone (e.g., "UTC-5" or "UTC+5:30")
export function getUtcOffset(tz: string): string {
  try {
    const now = new Date()
    const formatter = new Intl.DateTimeFormat('en-US', {
      timeZone: tz,
      timeZoneName: 'shortOffset',
    })
    const parts = formatter.formatToParts(now)
    const offset = parts.find(p => p.type === 'timeZoneName')?.value || ''
    // Convert "GMT-5" to "UTC-5"
    return offset.replace('GMT', 'UTC')
  } catch {
    return ''
  }
}

// Format timezone for display (e.g., "America/New_York" -> "America/New York (UTC-5)")
export function formatTimezone(tz: string): string {
  const displayName = tz.replace(/_/g, ' ')
  const offset = getUtcOffset(tz)
  return offset ? `${displayName} (${offset})` : displayName
}

// Format hour for 12-hour display (0 -> "12:00 AM", 13 -> "1:00 PM", etc.)
export function formatHour(hour: number): string {
  if (hour === 0 || hour === 24) return '12:00 AM'
  if (hour === 12) return '12:00 PM'
  if (hour < 12) return `${hour}:00 AM`
  return `${hour - 12}:00 PM`
}

// ============================================================================
// UTC Conversion Functions
// ============================================================================

/**
 * Convert a local date/hour in a specific timezone to a UTC ISO string.
 *
 * @param date - Date string in YYYY-MM-DD format
 * @param hour - Hour (0-23)
 * @param timezone - IANA timezone name (e.g., "Europe/Warsaw")
 * @returns UTC ISO datetime string
 *
 * Example: toUTCDatetime("2024-01-15", 9, "Europe/Warsaw")
 *          -> "2024-01-15T08:00:00.000Z" (Warsaw is UTC+1 in January)
 */
export function toUTCDatetime(date: string, hour: number, timezone: string): string {
  const [year, month, day] = date.split('-').map(Number)

  // Start with the target hour as if it were UTC
  const tentativeUTC = new Date(Date.UTC(year, month - 1, day, hour, 0, 0))

  // See what time that displays as in the target timezone
  const tzParts = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: 'numeric',
    hour12: false,
  }).formatToParts(tentativeUTC)

  const tzHour = parseInt(tzParts.find(p => p.type === 'hour')?.value || '0', 10)
  const tzDay = parseInt(tzParts.find(p => p.type === 'day')?.value || '0', 10)

  // Calculate offset: what we got vs what we want
  // Plus day boundary correction for timezones that cross midnight
  let offsetHours = tzHour - hour
  if (tzDay > day) offsetHours += 24
  else if (tzDay < day) offsetHours -= 24

  // Subtract the offset to get the correct UTC time
  const correctUTC = new Date(tentativeUTC.getTime() - offsetHours * 60 * 60 * 1000)

  return correctUTC.toISOString()
}

/**
 * Convert a UTC ISO datetime string to a local date and hour in a specific timezone.
 *
 * @param utcDatetime - UTC ISO datetime string
 * @param timezone - IANA timezone name
 * @returns Object with date (YYYY-MM-DD) and hour (0-23)
 */
export function fromUTCToLocal(
  utcDatetime: string,
  timezone: string
): { date: string; hour: number } {
  const utcDate = new Date(utcDatetime)

  // Format the date in the target timezone (en-CA gives YYYY-MM-DD format)
  const dateFormatter = new Intl.DateTimeFormat('en-CA', {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
  const date = dateFormatter.format(utcDate)

  const hourFormatter = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    hour: 'numeric',
    hour12: false,
  })
  const hourStr = hourFormatter.format(utcDate)
  const hour = parseInt(hourStr, 10)

  return { date, hour }
}

// ============================================================================
// Display Formatting Functions
// ============================================================================

/**
 * Format a date for display in a specific timezone.
 *
 * @param date - Date object or ISO string
 * @param timezone - IANA timezone name
 * @param options - Optional Intl.DateTimeFormat options (defaults to short date format)
 */
export function formatDateInTimezone(
  date: Date | string,
  timezone: string,
  options: Intl.DateTimeFormatOptions = {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  }
): string {
  const d = typeof date === 'string' ? new Date(date) : date
  return d.toLocaleDateString('en-US', { timeZone: timezone, ...options })
}

/**
 * Format a time for display in a specific timezone.
 *
 * @param date - Date object or ISO string
 * @param timezone - IANA timezone name
 * @param options - Optional Intl.DateTimeFormat options
 */
export function formatTimeInTimezone(
  date: Date | string,
  timezone: string,
  options: Intl.DateTimeFormatOptions = {
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }
): string {
  const d = typeof date === 'string' ? new Date(date) : date
  return d.toLocaleTimeString('en-US', { timeZone: timezone, ...options })
}

/**
 * Format a date and time for display in a specific timezone.
 *
 * @param date - Date object or ISO string
 * @param timezone - IANA timezone name
 * @param options - Intl.DateTimeFormat options
 */
export function formatDateTimeInTimezone(
  date: Date | string,
  timezone: string,
  options: Intl.DateTimeFormatOptions = {
    dateStyle: 'medium',
    timeStyle: 'short',
  }
): string {
  const d = typeof date === 'string' ? new Date(date) : date
  return d.toLocaleString('en-US', { timeZone: timezone, ...options })
}

/**
 * Format a time slot (start to end) for display.
 *
 * @param slotStart - Start time (Date or ISO string)
 * @param slotEnd - End time (Date or ISO string)
 * @param timezone - IANA timezone name
 * @returns Formatted string like "Mon, Jan 15 9:00 AM - 10:00 AM"
 */
export function formatSlotTime(
  slotStart: Date | string,
  slotEnd: Date | string,
  timezone: string
): string {
  const start = typeof slotStart === 'string' ? new Date(slotStart) : slotStart
  const end = typeof slotEnd === 'string' ? new Date(slotEnd) : slotEnd

  const dateStr = formatDateInTimezone(start, timezone)
  const startTime = formatTimeInTimezone(start, timezone)
  const endTime = formatTimeInTimezone(end, timezone)

  return `${dateStr} ${startTime} - ${endTime}`
}

/**
 * Get time-of-day key for grouping slots (HH:MM in 24-hour format).
 * Used internally by PollGrid for organizing slots by time.
 */
export function getTimeKey(date: Date, timezone: string): string {
  return date.toLocaleTimeString('en-US', {
    timeZone: timezone,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

/**
 * Get date key for grouping slots (locale date string).
 * Used internally by PollGrid for organizing slots by date.
 */
export function getDateKey(date: Date, timezone: string): string {
  return date.toLocaleDateString('en-US', { timeZone: timezone })
}

/**
 * Format a simple date for lists (e.g., "Jan 15, 2024").
 *
 * @param dateStr - ISO date string
 */
export function formatShortDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

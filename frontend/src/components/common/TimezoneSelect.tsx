import React from 'react'
import { COMMON_TIMEZONES, getBrowserTimezone, formatTimezone } from '../../utils/timezones'

interface TimezoneSelectProps {
  value: string
  onChange: (tz: string) => void
  id?: string
  label?: string
  className?: string
}

export const TimezoneSelect: React.FC<TimezoneSelectProps> = ({
  value,
  onChange,
  id = 'timezone',
  label = 'Timezone',
  className = 'poll-form-group',
}) => {
  const browserTz = getBrowserTimezone()

  return (
    <div className={className}>
      <label htmlFor={id}>{label}</label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {!COMMON_TIMEZONES.includes(browserTz) && (
          <option value={browserTz}>{formatTimezone(browserTz)}</option>
        )}
        {COMMON_TIMEZONES.map(tz => (
          <option key={tz} value={tz}>{formatTimezone(tz)}</option>
        ))}
      </select>
    </div>
  )
}

export default TimezoneSelect

import React from 'react'
import { TelemetryEvent } from '@/hooks/useUsage'
import { Badge, EmptyState, Table, type Tone } from '@/components/shared/ui'
import { formatCost } from '@/utils/format'
import { formatUserDate, formatUserDateTime, formatUserTime } from '@/utils/timezones'

interface RecentEventsTableProps {
  events: TelemetryEvent[]
}

export const RecentEventsTable: React.FC<RecentEventsTableProps> = ({ events }) => {
  if (events.length === 0) return <EmptyState message="No recent events" />

  return (
    <div className="max-h-64 overflow-auto">
      <Table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Event</th>
            <th>Source</th>
            <th className="text-right">Value</th>
          </tr>
        </thead>
        <tbody>
          {events.slice(0, 20).map(event => (
            <tr key={event.id}>
              <td className="text-slate-600 whitespace-nowrap">{formatTime(event.timestamp)}</td>
              <td><Badge tone={eventTone(event.name)}>{event.name}</Badge></td>
              <td className="text-slate-600 truncate max-w-32">{event.source || event.tool_name || '-'}</td>
              <td className="text-right font-mono text-slate-700">{formatValue(event.name, event.value)}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  )
}

// A time alone is ambiguous once the range spans days, so anything before today carries its date.
function formatTime(isoString: string): string {
  const time = { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false } as const
  return formatUserDate(isoString) === formatUserDate(new Date())
    ? formatUserTime(isoString, time)
    : formatUserDateTime(isoString, { month: 'short', day: 'numeric', ...time })
}

function eventTone(name: string): Tone {
  if (name.includes('error')) return 'danger'
  if (name.includes('token')) return 'info'
  if (name.includes('cost')) return 'success'
  if (name.includes('session')) return 'primary'
  return 'neutral'
}

function formatValue(name: string, value: number | null): string {
  if (value === null) return '-'
  if (name.includes('cost')) return formatCost(value)
  if (name.includes('token')) return value.toLocaleString()
  return value.toString()
}

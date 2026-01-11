import React from 'react'
import { TelemetryEvent } from '@/hooks/useTelemetry'

interface RecentEventsTableProps {
  events: TelemetryEvent[]
}

export const RecentEventsTable: React.FC<RecentEventsTableProps> = ({ events }) => {
  if (events.length === 0) {
    return (
      <div className="h-64 flex items-center justify-center text-slate-400">
        No recent events
      </div>
    )
  }

  return (
    <div className="overflow-auto max-h-64">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-slate-50">
          <tr className="text-left text-slate-600">
            <th className="py-2 px-2 font-medium">Time</th>
            <th className="py-2 px-2 font-medium">Event</th>
            <th className="py-2 px-2 font-medium">Source</th>
            <th className="py-2 px-2 font-medium text-right">Value</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {events.slice(0, 20).map(event => (
            <tr key={event.id} className="hover:bg-slate-50">
              <td className="py-2 px-2 text-slate-500 whitespace-nowrap">
                {formatTime(event.timestamp)}
              </td>
              <td className="py-2 px-2">
                <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                  getEventBadgeColor(event.name)
                }`}>
                  {event.name}
                </span>
              </td>
              <td className="py-2 px-2 text-slate-600 truncate max-w-32">
                {event.source || event.tool_name || '-'}
              </td>
              <td className="py-2 px-2 text-right font-mono text-slate-700">
                {formatValue(event.name, event.value)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function formatTime(isoString: string): string {
  const date = new Date(isoString)
  return date.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

function getEventBadgeColor(name: string): string {
  if (name.includes('token')) return 'bg-blue-100 text-blue-700'
  if (name.includes('cost')) return 'bg-green-100 text-green-700'
  if (name.includes('session')) return 'bg-purple-100 text-purple-700'
  if (name.includes('error')) return 'bg-red-100 text-red-700'
  return 'bg-slate-100 text-slate-700'
}

function formatValue(name: string, value: number | null): string {
  if (value === null) return '-'
  if (name.includes('cost')) return `$${value.toFixed(4)}`
  if (name.includes('token')) return value.toLocaleString()
  return value.toString()
}

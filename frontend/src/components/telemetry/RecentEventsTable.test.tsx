import { describe, it, expect } from 'vitest'
import { render, screen, within } from '@/test/utils'
import { RecentEventsTable } from './RecentEventsTable'
import type { TelemetryEvent } from '@/hooks/useTelemetry'

const makeEvent = (overrides: Partial<TelemetryEvent>): TelemetryEvent => ({
  id: 1,
  timestamp: '2026-05-29T12:00:00Z',
  event_type: 'metric',
  name: 'token.usage',
  value: 100,
  session_id: 's1',
  source: 'claude',
  tool_name: null,
  attributes: {},
  body: null,
  ...overrides,
})

describe('RecentEventsTable', () => {
  it('shows empty state when there are no events', () => {
    render(<RecentEventsTable events={[]} />)
    expect(screen.getByText('No recent events')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('renders a row per event with header columns', () => {
    const events = [
      makeEvent({ id: 1, name: 'token.usage' }),
      makeEvent({ id: 2, name: 'cost.usage' }),
    ]
    render(<RecentEventsTable events={events} />)
    expect(screen.getByText('Time')).toBeInTheDocument()
    expect(screen.getByText('Event')).toBeInTheDocument()
    expect(screen.getByText('Source')).toBeInTheDocument()
    expect(screen.getByText('Value')).toBeInTheDocument()
    // 2 body rows + 1 header row
    expect(screen.getAllByRole('row')).toHaveLength(3)
  })

  it('caps display at 20 rows even when more events are passed', () => {
    const events = Array.from({ length: 30 }, (_, i) => makeEvent({ id: i + 1 }))
    render(<RecentEventsTable events={events} />)
    // 20 body rows + 1 header
    expect(screen.getAllByRole('row')).toHaveLength(21)
  })

  it.each([
    ['cost.usage', 0.12345, '$0.1235'],
    ['token.usage', 12345, '12,345'],
    ['session.count', 7, '7'],
  ])('formats value for %s as %s', (name, value, expected) => {
    render(<RecentEventsTable events={[makeEvent({ name, value })]} />)
    expect(screen.getByText(expected)).toBeInTheDocument()
  })

  it('renders a dash for null values', () => {
    render(<RecentEventsTable events={[makeEvent({ name: 'token.usage', value: null })]} />)
    const row = screen.getAllByRole('row')[1]
    expect(within(row).getByText('-')).toBeInTheDocument()
  })

  it('falls back to tool_name then dash for the source column', () => {
    const events = [
      makeEvent({ id: 1, source: 'mysource', tool_name: 'Bash' }),
      makeEvent({ id: 2, source: null, tool_name: 'Edit' }),
      makeEvent({ id: 3, source: null, tool_name: null }),
    ]
    render(<RecentEventsTable events={events} />)
    expect(screen.getByText('mysource')).toBeInTheDocument()
    expect(screen.getByText('Edit')).toBeInTheDocument()
  })

  it('renders the event name as a badge label', () => {
    render(<RecentEventsTable events={[makeEvent({ name: 'session.count' })]} />)
    expect(screen.getByText('session.count')).toBeInTheDocument()
  })
})

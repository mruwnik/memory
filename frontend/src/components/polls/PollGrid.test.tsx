import { describe, it, expect, vi } from 'vitest'
import type { ComponentProps } from 'react'
import { render, renderWithUser, screen, within, fireEvent } from '@/test/utils'
import PollGrid from './PollGrid'
import type { AvailabilitySlot, SlotAggregation } from '@/hooks/usePolls'

// Use UTC so date/time-key grouping is deterministic regardless of the
// machine running the tests.
const TZ = 'UTC'

// 2024-01-15 09:00 -> 11:00 UTC, hourly slots => two slots (09:00, 10:00) on one day.
const START = new Date('2024-01-15T09:00:00.000Z')
const END = new Date('2024-01-15T11:00:00.000Z')

const SLOT_09 = '2024-01-15T09:00:00.000Z'
const SLOT_10 = '2024-01-15T10:00:00.000Z'

function renderGrid(props: Partial<ComponentProps<typeof PollGrid>> = {}) {
  const onSlotsChange = vi.fn()
  const result = renderWithUser(
    <PollGrid
      datetimeStart={START}
      datetimeEnd={END}
      slotDurationMinutes={60}
      displayTimezone={TZ}
      selectedSlots={[]}
      onSlotsChange={onSlotsChange}
      {...props}
    />,
  )
  return { onSlotsChange, ...result }
}

describe('PollGrid - structure', () => {
  it('renders a grid with one interactive button per generated slot', () => {
    renderGrid()
    // Two slots -> two interactive cell buttons.
    const buttons = screen.getAllByRole('button')
    expect(buttons).toHaveLength(2)
  })

  it('omits partial trailing slots that overrun the end time', () => {
    // 09:00 -> 10:30 with hourly slots: only the 09:00 slot fits fully.
    renderGrid({ datetimeEnd: new Date('2024-01-15T10:30:00.000Z') })
    expect(screen.getAllByRole('button')).toHaveLength(1)
  })

  it('produces more rows for finer slot durations', () => {
    renderGrid({ slotDurationMinutes: 30 })
    // 09:00 -> 11:00 in 30-min slots = 4 slots.
    expect(screen.getAllByRole('button')).toHaveLength(4)
  })

  it('labels unselected interactive cells as "not selected"', () => {
    renderGrid()
    expect(
      screen.getByLabelText(/9:00 AM on .*: not selected/),
    ).toBeInTheDocument()
  })

  it('exposes the grid region with an accessible name', () => {
    renderGrid()
    expect(
      screen.getByRole('grid', { name: 'Availability time grid' }),
    ).toBeInTheDocument()
  })
})

describe('PollGrid - selection legend', () => {
  it('shows the selection hint when interactive', () => {
    renderGrid()
    expect(
      screen.getByText(/Click, drag, or use arrow keys to select times/),
    ).toBeInTheDocument()
  })

  it('hides the selection hint when readonly', () => {
    renderGrid({ readonly: true })
    expect(
      screen.queryByText(/Click, drag, or use arrow keys to select times/),
    ).not.toBeInTheDocument()
  })

  it('hides the legend entirely when showLegend is false', () => {
    renderGrid({ showLegend: false })
    expect(screen.queryByText('Available')).not.toBeInTheDocument()
  })

  it('shows heatmap legend in heatmap mode', () => {
    renderGrid({ aggregatedData: [], readonly: true, onSlotsChange: undefined })
    expect(screen.getByText('No responses')).toBeInTheDocument()
    expect(screen.getByText('All available')).toBeInTheDocument()
    expect(screen.getByText('If needed only')).toBeInTheDocument()
  })
})

describe('PollGrid - mouse selection', () => {
  it('selects a slot on mouse down when not already selected', async () => {
    const { onSlotsChange } = renderGrid()
    const cell = screen.getByLabelText(/9:00 AM on .*: not selected/)
    fireEvent.mouseDown(cell)
    expect(onSlotsChange).toHaveBeenCalledWith([
      expect.objectContaining({ slot_start: SLOT_09, availability_level: 1 }),
    ])
  })

  it('uses availabilityLevel 2 ("if needed") when painting that level', () => {
    const { onSlotsChange } = renderGrid({ availabilityLevel: 2 })
    const cell = screen.getByLabelText(/9:00 AM on .*: not selected/)
    fireEvent.mouseDown(cell)
    expect(onSlotsChange).toHaveBeenCalledWith([
      expect.objectContaining({ slot_start: SLOT_09, availability_level: 2 }),
    ])
  })

  it('deselects a slot on mouse down when already selected', () => {
    const selected: AvailabilitySlot[] = [
      { slot_start: SLOT_09, slot_end: SLOT_10, availability_level: 1 },
    ]
    const { onSlotsChange } = renderGrid({ selectedSlots: selected })
    const cell = screen.getByLabelText(/9:00 AM on .*: selected as available/)
    fireEvent.mouseDown(cell)
    expect(onSlotsChange).toHaveBeenCalledWith([])
  })

  it('continues painting onto a new cell once a drag is in progress', () => {
    // After mousedown sets isDragging, a mouseenter on another cell paints it.
    // (selectedSlots is controlled by the parent; here both adds start from the
    // same empty baseline since the mock does not feed state back.)
    const { onSlotsChange, rerender } = renderGrid()
    const first = screen.getByLabelText(/9:00 AM on .*: not selected/)
    fireEvent.mouseDown(first)
    // Force a re-render so the isDragging state update is committed.
    rerender(
      <PollGrid
        datetimeStart={START}
        datetimeEnd={END}
        slotDurationMinutes={60}
        displayTimezone={TZ}
        selectedSlots={[]}
        onSlotsChange={onSlotsChange}
      />,
    )
    const second = screen.getByLabelText(/10:00 AM on .*: not selected/)
    fireEvent.mouseEnter(second)
    expect(onSlotsChange).toHaveBeenLastCalledWith([
      expect.objectContaining({ slot_start: SLOT_10 }),
    ])
  })

  it('does not paint on mouse enter when no drag is in progress', () => {
    const { onSlotsChange } = renderGrid()
    const cell = screen.getByLabelText(/9:00 AM on .*: not selected/)
    fireEvent.mouseEnter(cell)
    expect(onSlotsChange).not.toHaveBeenCalled()
  })

  it('renders a selected slot with the checkmark indicator', () => {
    const selected: AvailabilitySlot[] = [
      { slot_start: SLOT_09, slot_end: SLOT_10, availability_level: 1 },
    ]
    renderGrid({ selectedSlots: selected })
    const cell = screen.getByLabelText(/9:00 AM on .*: selected as available/)
    expect(within(cell).getByText('✓')).toBeInTheDocument()
  })

  it('renders an "if needed" slot with the tilde indicator', () => {
    const selected: AvailabilitySlot[] = [
      { slot_start: SLOT_09, slot_end: SLOT_10, availability_level: 2 },
    ]
    renderGrid({ selectedSlots: selected })
    const cell = screen.getByLabelText(/9:00 AM on .*: selected as if needed/)
    expect(within(cell).getByText('~')).toBeInTheDocument()
  })
})

describe('PollGrid - keyboard selection', () => {
  it('toggles the focused slot on Enter', async () => {
    const { user, onSlotsChange } = renderGrid()
    const cell = screen.getByLabelText(/9:00 AM on .*: not selected/)
    cell.focus()
    await user.keyboard('{Enter}')
    expect(onSlotsChange).toHaveBeenCalledWith([
      expect.objectContaining({ slot_start: SLOT_09 }),
    ])
  })

  it('toggles the focused slot on Space', async () => {
    const { user, onSlotsChange } = renderGrid()
    const cell = screen.getByLabelText(/9:00 AM on .*: not selected/)
    cell.focus()
    await user.keyboard(' ')
    expect(onSlotsChange).toHaveBeenCalledWith([
      expect.objectContaining({ slot_start: SLOT_09 }),
    ])
  })

  it('moves focus down with ArrowDown', async () => {
    const { user } = renderGrid()
    const first = screen.getByLabelText(/9:00 AM on .*: not selected/)
    first.focus()
    await user.keyboard('{ArrowDown}')
    expect(screen.getByLabelText(/10:00 AM on .*: not selected/)).toHaveFocus()
  })

  it('clamps focus at the top with ArrowUp', async () => {
    const { user } = renderGrid()
    const first = screen.getByLabelText(/9:00 AM on .*: not selected/)
    first.focus()
    await user.keyboard('{ArrowUp}')
    expect(first).toHaveFocus()
  })
})

describe('PollGrid - readonly mode', () => {
  it('renders gridcells without buttons and ignores clicks', () => {
    const selected: AvailabilitySlot[] = [
      { slot_start: SLOT_09, slot_end: SLOT_10, availability_level: 1 },
    ]
    const onSlotsChange = vi.fn()
    render(
      <PollGrid
        datetimeStart={START}
        datetimeEnd={END}
        slotDurationMinutes={60}
        displayTimezone={TZ}
        selectedSlots={selected}
        onSlotsChange={onSlotsChange}
        readonly
      />,
    )
    expect(screen.queryAllByRole('button')).toHaveLength(0)
    const cell = screen.getByLabelText(/9:00 AM on .*: selected as available/)
    fireEvent.mouseDown(cell)
    expect(onSlotsChange).not.toHaveBeenCalled()
  })
})

describe('PollGrid - heatmap mode', () => {
  const aggregated: SlotAggregation[] = [
    {
      slot_start: SLOT_09,
      slot_end: SLOT_10,
      available_count: 3,
      if_needed_count: 1,
      total_count: 4,
      respondents: ['Alice', 'Bob', 'Carol'],
    },
  ]

  it('renders the available count inside aggregated cells', () => {
    render(
      <PollGrid
        datetimeStart={START}
        datetimeEnd={END}
        slotDurationMinutes={60}
        displayTimezone={TZ}
        selectedSlots={[]}
        aggregatedData={aggregated}
        totalResponses={4}
        readonly
      />,
    )
    const cell = screen.getByLabelText(/9:00 AM on .*: 3 available, 1 if needed/)
    expect(within(cell).getByText('3')).toBeInTheDocument()
  })

  it('builds a respondents tooltip on aggregated cells', () => {
    render(
      <PollGrid
        datetimeStart={START}
        datetimeEnd={END}
        slotDurationMinutes={60}
        displayTimezone={TZ}
        selectedSlots={[]}
        aggregatedData={aggregated}
        totalResponses={4}
        readonly
      />,
    )
    const cell = screen.getByLabelText(/9:00 AM on .*: 3 available, 1 if needed/)
    expect(cell).toHaveAttribute('title', expect.stringContaining('Alice, Bob, Carol'))
  })

  it('renders no buttons in heatmap mode', () => {
    render(
      <PollGrid
        datetimeStart={START}
        datetimeEnd={END}
        slotDurationMinutes={60}
        displayTimezone={TZ}
        selectedSlots={[]}
        aggregatedData={aggregated}
        totalResponses={4}
      />,
    )
    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })
})

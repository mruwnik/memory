import React, { useState, useCallback, useMemo, useRef, useEffect } from 'react'
import type { AvailabilitySlot, AvailabilityLevel, SlotAggregation } from '../../hooks/usePolls'
import { getTimeKey, getDateKey, formatDateInTimezone } from '../../utils/timezones'

interface TimeSlot {
  start: Date
  end: Date
  key: string
}

interface PollGridProps {
  datetimeStart: Date  // UTC datetime
  datetimeEnd: Date    // UTC datetime
  slotDurationMinutes: number
  displayTimezone: string  // For display only
  // Selection mode
  selectedSlots: AvailabilitySlot[]
  onSlotsChange?: (slots: AvailabilitySlot[]) => void
  // Heatmap mode (view results)
  aggregatedData?: SlotAggregation[]
  totalResponses?: number
  // Display options
  readonly?: boolean
  showLegend?: boolean
  availabilityLevel?: AvailabilityLevel // Current level being painted
}

interface GridData {
  slots: (TimeSlot | null)[][]  // [day][timeSlotIndex], null if no slot at that time
  timeKeys: string[]             // sorted time keys for labels
  dateKeys: string[]             // sorted date keys for headers
}

// Generate all time slots for the grid based on UTC datetime range
// Returns slots organized as [day][timeSlotIndex] where each row represents
// the same time-of-day across all days. Cells are null where no slot exists.
function generateTimeSlots(
  datetimeStart: Date,
  datetimeEnd: Date,
  slotDurationMinutes: number,
  displayTimezone: string
): GridData {
  const slotMs = slotDurationMinutes * 60 * 1000

  // First pass: collect all slots grouped by date
  const dayMap = new Map<string, Map<string, TimeSlot>>() // date -> timeKey -> slot
  const allTimeKeys = new Set<string>()

  let currentTime = new Date(datetimeStart)
  while (currentTime < datetimeEnd) {
    const slotEnd = new Date(currentTime.getTime() + slotMs)

    // Don't create partial slots that go past the end
    if (slotEnd > datetimeEnd) break

    const dateKey = getDateKey(currentTime, displayTimezone)
    const timeKey = getTimeKey(currentTime, displayTimezone)

    if (!dayMap.has(dateKey)) {
      dayMap.set(dateKey, new Map())
    }

    dayMap.get(dateKey)!.set(timeKey, {
      start: new Date(currentTime),
      end: slotEnd,
      key: currentTime.toISOString(),
    })

    allTimeKeys.add(timeKey)
    currentTime = slotEnd
  }

  // Sort dates chronologically
  const sortedDates = Array.from(dayMap.keys()).sort((a, b) => {
    return new Date(a).getTime() - new Date(b).getTime()
  })

  // Sort time keys chronologically
  const sortedTimeKeys = Array.from(allTimeKeys).sort()

  // Build the grid: for each day, create array with slots at their correct positions
  // Use null for times where that day doesn't have a slot
  const slots: (TimeSlot | null)[][] = []

  for (const dateKey of sortedDates) {
    const daySlots: (TimeSlot | null)[] = []
    const dayTimeMap = dayMap.get(dateKey)!

    for (const timeKey of sortedTimeKeys) {
      const slot = dayTimeMap.get(timeKey)
      daySlots.push(slot || null)
    }

    slots.push(daySlots)
  }

  return { slots, timeKeys: sortedTimeKeys, dateKeys: sortedDates }
}

// Get heatmap color based on availability
function getHeatmapColor(available: number, ifNeeded: number, total: number, maxResponses: number): string {
  if (maxResponses === 0) return '#f1f5f9' // slate-100

  const ratio = available / maxResponses
  const ifNeededRatio = ifNeeded / maxResponses

  if (available === maxResponses) {
    return '#22c55e' // green-500 - everyone available
  } else if (available > 0) {
    // Gradient from light to dark green based on ratio
    const lightness = 90 - (ratio * 40)
    return `hsl(142, 76%, ${lightness}%)`
  } else if (ifNeeded > 0) {
    // Yellow for "if needed" only
    const lightness = 90 - (ifNeededRatio * 30)
    return `hsl(48, 96%, ${lightness}%)`
  }

  return '#f1f5f9' // slate-100
}

// Format time key into 12h label
function formatTimeLabel(timeKey: string): string {
  const [hours, minutes] = timeKey.split(':').map(Number)
  const h = hours % 12 || 12
  const ampm = hours < 12 ? 'AM' : 'PM'
  return `${h}:${minutes.toString().padStart(2, '0')} ${ampm}`
}

export const PollGrid: React.FC<PollGridProps> = ({
  datetimeStart,
  datetimeEnd,
  slotDurationMinutes,
  displayTimezone,
  selectedSlots,
  onSlotsChange,
  aggregatedData,
  totalResponses = 0,
  readonly = false,
  showLegend = true,
  availabilityLevel = 1,
}) => {
  const [isDragging, setIsDragging] = useState(false)
  const [dragMode, setDragMode] = useState<'select' | 'deselect'>('select')
  // Roving tabIndex: track which [dayIndex, rowIndex] cell is keyboard-focusable
  const [activeCell, setActiveCell] = useState<[number, number]>([0, 0])
  const gridRef = useRef<HTMLDivElement>(null)

  // Generate time slots grid
  const gridData = useMemo(() => {
    return generateTimeSlots(datetimeStart, datetimeEnd, slotDurationMinutes, displayTimezone)
  }, [datetimeStart, datetimeEnd, slotDurationMinutes, displayTimezone])

  // Create a map of selected slots for O(1) lookup
  const selectedMap = useMemo(() => {
    const map = new Map<string, AvailabilitySlot>()
    for (const slot of selectedSlots) {
      map.set(new Date(slot.slot_start).toISOString(), slot)
    }
    return map
  }, [selectedSlots])

  // Create a map of aggregated data for O(1) lookup
  // Key includes both start and end to handle any duration mismatches
  const aggregatedMap = useMemo(() => {
    const map = new Map<string, SlotAggregation>()
    if (aggregatedData) {
      for (const slot of aggregatedData) {
        const key = `${new Date(slot.slot_start).toISOString()}|${new Date(slot.slot_end).toISOString()}`
        map.set(key, slot)
      }
    }
    return map
  }, [aggregatedData])

  // Format time keys for display labels
  const timeLabels = useMemo(() => {
    return gridData.timeKeys.map(formatTimeLabel)
  }, [gridData.timeKeys])

  // Toggle a single slot (used by both mouse and keyboard)
  const toggleSlot = useCallback((slot: TimeSlot) => {
    if (readonly || !onSlotsChange) return
    const isSelected = selectedMap.has(slot.key)
    if (isSelected) {
      onSlotsChange(selectedSlots.filter(s => new Date(s.slot_start).toISOString() !== slot.key))
    } else {
      onSlotsChange([
        ...selectedSlots,
        {
          slot_start: slot.start.toISOString(),
          slot_end: slot.end.toISOString(),
          availability_level: availabilityLevel,
        },
      ])
    }
  }, [readonly, onSlotsChange, selectedMap, selectedSlots, availabilityLevel])

  // Handle slot click/drag (mouse/touch)
  const handleSlotInteraction = useCallback((slot: TimeSlot, isStart: boolean) => {
    if (readonly || !onSlotsChange) return

    const isSelected = selectedMap.has(slot.key)

    // Compute the effective mode for this interaction
    // On start: determine from current selection state
    // During drag: use the stored dragMode
    let effectiveMode = dragMode
    if (isStart) {
      effectiveMode = isSelected ? 'deselect' : 'select'
      setDragMode(effectiveMode)
      setIsDragging(true)
    }

    if (effectiveMode === 'select' && !isSelected) {
      onSlotsChange([
        ...selectedSlots,
        {
          slot_start: slot.start.toISOString(),
          slot_end: slot.end.toISOString(),
          availability_level: availabilityLevel,
        },
      ])
    } else if (effectiveMode === 'deselect' && isSelected) {
      onSlotsChange(selectedSlots.filter(s => new Date(s.slot_start).toISOString() !== slot.key))
    }
  }, [readonly, onSlotsChange, selectedMap, selectedSlots, dragMode, availabilityLevel])

  // Mouse event handlers
  const handleMouseDown = useCallback((slot: TimeSlot) => {
    handleSlotInteraction(slot, true)
  }, [handleSlotInteraction])

  const handleMouseEnter = useCallback((slot: TimeSlot) => {
    if (isDragging) {
      handleSlotInteraction(slot, false)
    }
  }, [isDragging, handleSlotInteraction])

  const handleMouseUp = useCallback(() => {
    setIsDragging(false)
  }, [])

  // Global mouse up listener
  useEffect(() => {
    window.addEventListener('mouseup', handleMouseUp)
    return () => window.removeEventListener('mouseup', handleMouseUp)
  }, [handleMouseUp])

  // Touch event handlers for mobile
  const handleTouchStart = useCallback((slot: TimeSlot) => {
    handleSlotInteraction(slot, true)
  }, [handleSlotInteraction])

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    if (!isDragging || !gridRef.current) return

    const touch = e.touches[0]
    const element = document.elementFromPoint(touch.clientX, touch.clientY)
    const slotKey = element?.getAttribute('data-slot-key')

    if (slotKey) {
      const allSlots = gridData.slots.flat().filter((s): s is TimeSlot => s !== null)
      const slot = allSlots.find(s => s.key === slotKey)
      if (slot) {
        handleSlotInteraction(slot, false)
      }
    }
  }, [isDragging, gridData.slots, handleSlotInteraction])

  // Keyboard navigation handler (roving tabIndex within the grid)
  const handleKeyDown = useCallback((e: React.KeyboardEvent, dayIndex: number, rowIndex: number) => {
    const numDays = gridData.slots.length
    const numRows = gridData.timeKeys.length

    let newDay = dayIndex
    let newRow = rowIndex

    switch (e.key) {
      case 'ArrowRight':
        e.preventDefault()
        newDay = Math.min(dayIndex + 1, numDays - 1)
        break
      case 'ArrowLeft':
        e.preventDefault()
        newDay = Math.max(dayIndex - 1, 0)
        break
      case 'ArrowDown':
        e.preventDefault()
        newRow = Math.min(rowIndex + 1, numRows - 1)
        break
      case 'ArrowUp':
        e.preventDefault()
        newRow = Math.max(rowIndex - 1, 0)
        break
      case 'Home':
        e.preventDefault()
        newDay = 0
        break
      case 'End':
        e.preventDefault()
        newDay = numDays - 1
        break
      case ' ':
      case 'Enter': {
        e.preventDefault()
        const slot = gridData.slots[dayIndex]?.[rowIndex]
        if (slot) toggleSlot(slot)
        return
      }
      default:
        return
    }

    setActiveCell([newDay, newRow])
    // Focus the button at the new position
    gridRef.current
      ?.querySelector<HTMLElement>(`[data-day="${newDay}"][data-row="${newRow}"]`)
      ?.focus()
  }, [gridData.slots, gridData.timeKeys.length, toggleSlot])

  // Render mode check
  const isHeatmapMode = !!aggregatedData

  return (
    <div className="space-y-4">
      {showLegend && (
        <div className="flex flex-wrap gap-4 text-sm text-slate-600">
          {isHeatmapMode ? (
            <>
              <span className="flex items-center gap-1.5">
                <span className="w-4 h-4 rounded bg-slate-100" aria-hidden="true" />
                No responses
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-4 h-4 rounded bg-green-400" aria-hidden="true" />
                Some available
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-4 h-4 rounded bg-green-500" aria-hidden="true" />
                All available
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-4 h-4 rounded bg-yellow-300" aria-hidden="true" />
                If needed only
              </span>
            </>
          ) : (
            <>
              <span className="flex items-center gap-1.5">
                <span className="w-4 h-4 rounded bg-green-500" aria-hidden="true" />
                <span aria-hidden="true">✓</span> Available
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-4 h-4 rounded bg-yellow-400" aria-hidden="true" />
                <span aria-hidden="true">~</span> If needed
              </span>
              {!readonly && (
                <span className="text-slate-500">
                  Click, drag, or use arrow keys to select times; Space/Enter to toggle
                </span>
              )}
            </>
          )}
        </div>
      )}

      <div
        ref={gridRef}
        role="grid"
        aria-label="Availability time grid"
        aria-readonly={readonly || undefined}
        className="grid gap-px bg-slate-200 rounded-lg overflow-hidden"
        style={{
          gridTemplateColumns: `auto repeat(${gridData.slots.length}, 1fr)`,
        }}
        onMouseUp={handleMouseUp}
        onTouchEnd={handleMouseUp}
        onTouchMove={handleTouchMove}
      >
        {/* Header row - dates */}
        <div role="row" className="contents">
          <div role="columnheader" className="bg-slate-50 p-2" aria-label="Time" />
          {gridData.dateKeys.map((dateKey, dayIndex) => (
            <div
              key={dayIndex}
              role="columnheader"
              className="bg-slate-50 p-2 text-center text-xs font-medium text-slate-700"
            >
              {formatDateInTimezone(dateKey, displayTimezone)}
            </div>
          ))}
        </div>

        {/* Time rows */}
        {gridData.timeKeys.map((_timeKey, rowIndex) => (
          <div key={rowIndex} role="row" className="contents">
            <div
              role="rowheader"
              className="bg-slate-50 p-2 text-xs text-slate-500 text-right whitespace-nowrap"
            >
              {timeLabels[rowIndex] || ''}
            </div>
            {gridData.slots.map((daySlots, dayIndex) => {
              const slot = daySlots[rowIndex]
              const dateLabel = formatDateInTimezone(gridData.dateKeys[dayIndex], displayTimezone)
              const timeLabel = timeLabels[rowIndex] || ''

              if (!slot) {
                return (
                  <div
                    key={dayIndex}
                    role="gridcell"
                    aria-label={`${timeLabel} on ${dateLabel}: not available`}
                    className="bg-slate-100"
                  />
                )
              }

              const selectedSlot = selectedMap.get(slot.key)
              const isSelected = !!selectedSlot
              const aggregatedKey = `${slot.start.toISOString()}|${slot.end.toISOString()}`
              const aggregated = aggregatedMap.get(aggregatedKey)

              let cellStyle: React.CSSProperties = {}

              // Compute color class separately so it can be shared between
              // the read-only div and the interactive button
              let colorClass = 'bg-white'
              if (isHeatmapMode && aggregated) {
                cellStyle.background = getHeatmapColor(
                  aggregated.available_count,
                  aggregated.if_needed_count,
                  aggregated.total_count,
                  totalResponses
                )
                colorClass = ''
              } else if (isSelected) {
                colorClass = selectedSlot?.availability_level === 2 ? 'bg-yellow-400' : 'bg-green-500'
              }

              const baseCellClass = `min-h-[32px] flex items-center justify-center text-xs font-medium transition-colors ${colorClass}`

              // Build accessible label
              let ariaLabel: string
              if (isHeatmapMode && aggregated) {
                ariaLabel = `${timeLabel} on ${dateLabel}: ${aggregated.available_count} available, ${aggregated.if_needed_count} if needed`
              } else if (isSelected) {
                const levelLabel = selectedSlot?.availability_level === 2 ? 'if needed' : 'available'
                ariaLabel = `${timeLabel} on ${dateLabel}: selected as ${levelLabel}`
              } else {
                ariaLabel = `${timeLabel} on ${dateLabel}: not selected`
              }

              const isActive = activeCell[0] === dayIndex && activeCell[1] === rowIndex

              if (readonly || isHeatmapMode) {
                return (
                  <div
                    key={dayIndex}
                    role="gridcell"
                    aria-label={ariaLabel}
                    className={baseCellClass}
                    style={cellStyle}
                    title={
                      aggregated
                        ? `${aggregated.available_count} available, ${aggregated.if_needed_count} if needed\n${aggregated.respondents.join(', ')}`
                        : undefined
                    }
                  >
                    {isHeatmapMode && aggregated && aggregated.total_count > 0 && (
                      <span className="text-slate-700">{aggregated.available_count}</span>
                    )}
                  </div>
                )
              }

              // Interactive cell: button for keyboard + screen reader support
              const buttonClass = `w-full h-full min-h-[32px] flex items-center justify-center text-xs font-medium transition-colors cursor-pointer hover:opacity-80 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-inset ${colorClass}`

              return (
                <div key={dayIndex} role="gridcell">
                  <button
                    type="button"
                    aria-pressed={isSelected}
                    aria-label={ariaLabel}
                    tabIndex={isActive ? 0 : -1}
                    data-slot-key={slot.key}
                    data-day={dayIndex}
                    data-row={rowIndex}
                    className={buttonClass}
                    style={cellStyle}
                    onMouseDown={() => handleMouseDown(slot)}
                    onMouseEnter={() => handleMouseEnter(slot)}
                    onTouchStart={() => handleTouchStart(slot)}
                    onFocus={() => setActiveCell([dayIndex, rowIndex])}
                    onKeyDown={(e) => handleKeyDown(e, dayIndex, rowIndex)}
                  >
                    {/* Text indicator supplements color (WCAG 1.4.1 Use of Color) */}
                    {isSelected && (
                      <span aria-hidden="true" className="select-none">
                        {selectedSlot?.availability_level === 2 ? '~' : '✓'}
                      </span>
                    )}
                  </button>
                </div>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}

export default PollGrid

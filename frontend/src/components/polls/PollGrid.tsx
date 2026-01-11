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

  // Format time keys for display (convert 24h to 12h format)
  const timeLabels = useMemo(() => {
    return gridData.timeKeys.map(timeKey => {
      const [hours, minutes] = timeKey.split(':').map(Number)
      const h = hours % 12 || 12
      const ampm = hours < 12 ? 'AM' : 'PM'
      return `${h}:${minutes.toString().padStart(2, '0')} ${ampm}`
    })
  }, [gridData.timeKeys])

  // Handle slot click/drag
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
      // Add slot
      onSlotsChange([
        ...selectedSlots,
        {
          slot_start: slot.start.toISOString(),
          slot_end: slot.end.toISOString(),
          availability_level: availabilityLevel,
        },
      ])
    } else if (effectiveMode === 'deselect' && isSelected) {
      // Remove slot
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

  // Render mode check
  const isHeatmapMode = !!aggregatedData

  return (
    <div className="space-y-4">
      {showLegend && (
        <div className="flex flex-wrap gap-4 text-sm text-slate-600">
          {isHeatmapMode ? (
            <>
              <span className="flex items-center gap-1.5">
                <span className="w-4 h-4 rounded bg-slate-100" />
                No responses
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-4 h-4 rounded bg-green-400" />
                Some available
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-4 h-4 rounded bg-green-500" />
                All available
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-4 h-4 rounded bg-yellow-300" />
                If needed only
              </span>
            </>
          ) : (
            <>
              <span className="flex items-center gap-1.5">
                <span className="w-4 h-4 rounded bg-green-500" />
                Available
              </span>
              <span className="flex items-center gap-1.5">
                <span className="w-4 h-4 rounded bg-yellow-400" />
                If needed
              </span>
              <span className="text-slate-500">Click and drag to select times</span>
            </>
          )}
        </div>
      )}

      <div
        ref={gridRef}
        className="grid gap-px bg-slate-200 rounded-lg overflow-hidden"
        style={{
          gridTemplateColumns: `auto repeat(${gridData.slots.length}, 1fr)`,
        }}
        onMouseUp={handleMouseUp}
        onTouchEnd={handleMouseUp}
        onTouchMove={handleTouchMove}
      >
        {/* Header row - dates */}
        <div className="bg-slate-50 p-2" />
        {gridData.dateKeys.map((dateKey, dayIndex) => (
          <div key={dayIndex} className="bg-slate-50 p-2 text-center text-xs font-medium text-slate-700">
            {formatDateInTimezone(dateKey, displayTimezone)}
          </div>
        ))}

        {/* Time rows */}
        {gridData.timeKeys.map((_, rowIndex) => (
          <React.Fragment key={rowIndex}>
            <div className="bg-slate-50 p-2 text-xs text-slate-500 text-right whitespace-nowrap">
              {timeLabels[rowIndex] || ''}
            </div>
            {gridData.slots.map((daySlots, dayIndex) => {
              const slot = daySlots[rowIndex]
              if (!slot) return <div key={dayIndex} className="bg-slate-100" />

              const selectedSlot = selectedMap.get(slot.key)
              const isSelected = !!selectedSlot
              // Use composite key (start|end) for aggregated data lookup
              const aggregatedKey = `${slot.start.toISOString()}|${slot.end.toISOString()}`
              const aggregated = aggregatedMap.get(aggregatedKey)

              let cellStyle: React.CSSProperties = {}
              let cellClass = 'min-h-[32px] flex items-center justify-center text-xs font-medium transition-colors'

              if (isHeatmapMode && aggregated) {
                cellStyle.background = getHeatmapColor(
                  aggregated.available_count,
                  aggregated.if_needed_count,
                  aggregated.total_count,
                  totalResponses
                )
              } else if (isSelected) {
                cellClass += selectedSlot?.availability_level === 2 ? ' bg-yellow-400' : ' bg-green-500'
              } else {
                cellClass += ' bg-white'
              }

              if (!readonly) {
                cellClass += ' cursor-pointer hover:opacity-80'
              }

              return (
                <div
                  key={dayIndex}
                  className={cellClass}
                  style={cellStyle}
                  data-slot-key={slot.key}
                  onMouseDown={() => handleMouseDown(slot)}
                  onMouseEnter={() => handleMouseEnter(slot)}
                  onTouchStart={() => handleTouchStart(slot)}
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
            })}
          </React.Fragment>
        ))}
      </div>
    </div>
  )
}

export default PollGrid

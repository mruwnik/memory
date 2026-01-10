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
  if (maxResponses === 0) return '#f3f4f6' // gray-100

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

  return '#f3f4f6' // gray-100
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
    <div className="poll-grid-container">
      {showLegend && (
        <div className="poll-grid-legend">
          {isHeatmapMode ? (
            <>
              <span className="legend-item">
                <span className="legend-color" style={{ background: '#f3f4f6' }} />
                No responses
              </span>
              <span className="legend-item">
                <span className="legend-color" style={{ background: 'hsl(142, 76%, 70%)' }} />
                Some available
              </span>
              <span className="legend-item">
                <span className="legend-color" style={{ background: '#22c55e' }} />
                All available
              </span>
              <span className="legend-item">
                <span className="legend-color" style={{ background: 'hsl(48, 96%, 75%)' }} />
                If needed only
              </span>
            </>
          ) : (
            <>
              <span className="legend-item">
                <span className="legend-color" style={{ background: '#22c55e' }} />
                Available
              </span>
              <span className="legend-item">
                <span className="legend-color" style={{ background: '#facc15' }} />
                If needed
              </span>
              <span className="legend-tip">Click and drag to select times</span>
            </>
          )}
        </div>
      )}

      <div
        ref={gridRef}
        className="poll-grid"
        style={{
          gridTemplateColumns: `auto repeat(${gridData.slots.length}, 1fr)`,
        }}
        onMouseUp={handleMouseUp}
        onTouchEnd={handleMouseUp}
        onTouchMove={handleTouchMove}
      >
        {/* Header row - dates */}
        <div className="poll-grid-header-corner" />
        {gridData.dateKeys.map((dateKey, dayIndex) => (
          <div key={dayIndex} className="poll-grid-header">
            {formatDateInTimezone(dateKey, displayTimezone)}
          </div>
        ))}

        {/* Time rows */}
        {gridData.timeKeys.map((_, rowIndex) => (
          <React.Fragment key={rowIndex}>
            <div className="poll-grid-time-label">
              {timeLabels[rowIndex] || ''}
            </div>
            {gridData.slots.map((daySlots, dayIndex) => {
              const slot = daySlots[rowIndex]
              if (!slot) return <div key={dayIndex} className="poll-grid-cell empty" />

              const selectedSlot = selectedMap.get(slot.key)
              const isSelected = !!selectedSlot
              // Use composite key (start|end) for aggregated data lookup
              const aggregatedKey = `${slot.start.toISOString()}|${slot.end.toISOString()}`
              const aggregated = aggregatedMap.get(aggregatedKey)

              let cellStyle: React.CSSProperties = {}
              let cellClass = 'poll-grid-cell'

              if (isHeatmapMode && aggregated) {
                cellStyle.background = getHeatmapColor(
                  aggregated.available_count,
                  aggregated.if_needed_count,
                  aggregated.total_count,
                  totalResponses
                )
                cellClass += ' heatmap'
              } else if (isSelected) {
                cellClass += selectedSlot?.availability_level === 2 ? ' if-needed' : ' selected'
              }

              if (!readonly) {
                cellClass += ' interactive'
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
                    <span className="poll-grid-count">{aggregated.available_count}</span>
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

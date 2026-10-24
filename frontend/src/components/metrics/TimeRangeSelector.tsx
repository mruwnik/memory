import { TimeRange } from '@/hooks/useMetrics'
import { ToggleGroup } from '@/components/shared/ui'

const RANGES: { value: TimeRange; label: string }[] = [
  { value: '1h', label: '1 hour' },
  { value: '6h', label: '6 hours' },
  { value: '24h', label: '24 hours' },
  { value: '7d', label: '7 days' },
]

export const TimeRangeSelector = ({ value, onChange }: { value: TimeRange; onChange: (range: TimeRange) => void }) => (
  <ToggleGroup label="Time range" options={RANGES} value={value} onChange={onChange} />
)

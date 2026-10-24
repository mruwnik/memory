import { useState, useEffect, useCallback, useRef } from 'react'
import { useDockerLogs, ContainerInfo, LogsResponse } from '../../hooks/useDockerLogs'
import { Badge, Card, Checkbox, Input, Notice, Page, RefreshButton, Tabs, ToggleGroup, Toolbar } from '@/components/shared/ui'

// Custom hook for debouncing values
function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value)

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedValue(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])

  return debouncedValue
}

type TimeRange = '15m' | '1h' | '6h' | '24h'
type RefreshInterval = 'off' | '5s' | '10s' | '30s'

const TIME_RANGES: { value: TimeRange; label: string; ms: number }[] = [
  { value: '15m', label: '15 min', ms: 15 * 60 * 1000 },
  { value: '1h', label: '1 hour', ms: 60 * 60 * 1000 },
  { value: '6h', label: '6 hours', ms: 6 * 60 * 60 * 1000 },
  { value: '24h', label: '24 hours', ms: 24 * 60 * 60 * 1000 },
]

const REFRESH_INTERVALS: { value: RefreshInterval; label: string; ms: number | null }[] = [
  { value: 'off', label: 'Off', ms: null },
  { value: '5s', label: '5s', ms: 5000 },
  { value: '10s', label: '10s', ms: 10000 },
  { value: '30s', label: '30s', ms: 30000 },
]

function getDisplayName(name: string): string {
  if (name.includes('api')) return 'API'
  if (name.includes('worker')) return 'Worker'
  if (name.includes('ingest')) return 'Ingest Hub'
  return name
}

interface LogLineProps {
  line: string
  filterText: string
}

function LogLine({ line, filterText }: LogLineProps) {
  let colorClass = 'text-slate-100'
  if (line.includes('ERROR') || line.includes('error')) {
    colorClass = 'text-red-400'
  } else if (line.includes('WARNING') || line.includes('warn')) {
    colorClass = 'text-yellow-400'
  } else if (line.includes('INFO') || line.includes('info')) {
    colorClass = 'text-green-400'
  } else if (line.includes('DEBUG') || line.includes('debug')) {
    colorClass = 'text-slate-500'
  }

  if (filterText && line.toLowerCase().includes(filterText.toLowerCase())) {
    const regex = new RegExp(`(${filterText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi')
    const parts = line.split(regex)
    return (
      <div className={colorClass}>
        {parts.map((part, i) =>
          part.toLowerCase() === filterText.toLowerCase() ? (
            <mark key={i} className="bg-yellow-500/30 text-yellow-300">{part}</mark>
          ) : (
            <span key={i}>{part}</span>
          )
        )}
      </div>
    )
  }

  return <div className={colorClass}>{line}</div>
}

export default function DockerLogs() {
  const { listContainers, getLogs } = useDockerLogs()

  const [containers, setContainers] = useState<ContainerInfo[]>([])
  const [selectedContainer, setSelectedContainer] = useState<string | null>(null)
  const [logs, setLogs] = useState<LogsResponse | null>(null)
  const [timeRange, setTimeRange] = useState<TimeRange>('1h')
  const [refreshInterval, setRefreshInterval] = useState<RefreshInterval>('off')
  const [filterText, setFilterText] = useState('')
  const [autoScroll, setAutoScroll] = useState(true)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Debounce filter text to avoid excessive API calls on every keystroke
  const debouncedFilterText = useDebounce(filterText, 300)

  const logViewerRef = useRef<HTMLPreElement>(null)
  const refreshIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const [containersError, setContainersError] = useState<string | null>(null)

  const loadContainers = useCallback(async () => {
    setContainersError(null)
    try {
      const data = await listContainers()
      setContainers(data)
      setSelectedContainer(prev => prev ?? (data.length > 0 ? data[0].name : null))
    } catch (err) {
      setContainersError(err instanceof Error ? err.message : 'Failed to list containers')
    }
  }, [listContainers])

  useEffect(() => {
    loadContainers()
  }, [loadContainers])

  // Load logs when container or time range changes
  const loadLogs = useCallback(async () => {
    if (!selectedContainer) return

    setLoading(true)
    setError(null)

    try {
      const range = TIME_RANGES.find(r => r.value === timeRange)
      const since = new Date(Date.now() - (range?.ms ?? 60 * 60 * 1000))

      const data = await getLogs(selectedContainer, {
        since,
        tail: 2000,
        filter_text: debouncedFilterText || undefined,
        timestamps: true,
      })

      setLogs(data)

      if (autoScroll && logViewerRef.current) {
        requestAnimationFrame(() => {
          logViewerRef.current?.scrollTo(0, logViewerRef.current.scrollHeight)
        })
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load logs')
    } finally {
      setLoading(false)
    }
  }, [selectedContainer, timeRange, debouncedFilterText, getLogs, autoScroll])

  useEffect(() => {
    loadLogs()
  }, [loadLogs])

  // Auto-refresh
  useEffect(() => {
    if (refreshIntervalRef.current) {
      clearInterval(refreshIntervalRef.current)
    }

    const interval = REFRESH_INTERVALS.find(r => r.value === refreshInterval)
    if (interval?.ms) {
      refreshIntervalRef.current = setInterval(loadLogs, interval.ms)
    }

    return () => {
      if (refreshIntervalRef.current) {
        clearInterval(refreshIntervalRef.current)
      }
    }
  }, [refreshInterval, loadLogs])

  return (
    <Page
      title="Docker logs"
      width="wide"
      error={containersError}
      onRetry={loadContainers}
    >
      <Toolbar actions={<RefreshButton onClick={loadLogs} loading={loading} label="Refresh logs" />}>
        <ToggleGroup label="Time range" options={TIME_RANGES} value={timeRange} onChange={setTimeRange} />
        <span className="text-sm text-slate-600">Auto:</span>
        <ToggleGroup label="Auto refresh" options={REFRESH_INTERVALS} value={refreshInterval} onChange={setRefreshInterval} />
      </Toolbar>

      {error && (
        <Notice tone="error" onRetry={loadLogs}>{error}</Notice>
      )}

      <Card flush className="overflow-hidden">
        <Tabs label="Containers" value={selectedContainer ?? ""} onChange={setSelectedContainer}
          options={containers.map(c => ({
            value: c.name,
            label: <>{getDisplayName(c.name)} <Badge tone={c.status.toLowerCase().includes('up') ? 'success' : 'neutral'}>{c.status}</Badge></>,
          }))} />

        <div className="p-4 border-b border-slate-200 flex flex-wrap gap-4 items-center">
          <Input
            type="search"
            placeholder="Filter logs..."
            aria-label="Filter logs"
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            className="sm:w-72"
          />

          <Checkbox checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)} label="Auto-scroll" />

          {logs && (
            <span className="text-sm text-slate-600">
              {logs.lines.toLocaleString()} lines
            </span>
          )}
        </div>

        <pre
          ref={logViewerRef}
          className="bg-slate-900 text-slate-100 p-4 font-mono text-sm overflow-auto h-[60vh] min-h-80 whitespace-pre-wrap"
        >
          {loading && !logs ? (
            <span className="text-slate-400">Loading logs...</span>
          ) : logs?.logs ? (
            logs.logs.split('\n').map((line, i) => (
              <LogLine key={i} line={line} filterText={filterText} />
            ))
          ) : !error && (
            <span className="text-slate-400">No logs available</span>
          )}
        </pre>
      </Card>
    </Page>
  )
}

import { useState, useEffect, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import { useDockerLogs, ContainerInfo, LogsResponse } from '../../hooks/useDockerLogs'
import { styles, cx } from '../sources/styles'

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

  // Load containers on mount only
  useEffect(() => {
    let mounted = true
    listContainers()
      .then(data => {
        if (mounted) {
          setContainers(data)
          // Auto-select first container if none selected
          setSelectedContainer(prev => prev ?? (data.length > 0 ? data[0].name : null))
        }
      })
      .catch(err => {
        if (mounted) setError(err.message)
      })
    return () => { mounted = false }
  }, [listContainers])

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

  const tabClass = (container: string) =>
    cx(
      'py-3 px-6 text-sm font-medium transition-colors border-b-2 -mb-px',
      selectedContainer === container
        ? 'border-primary text-primary bg-slate-50'
        : 'border-transparent text-slate-600 hover:text-slate-800 hover:bg-slate-50'
    )

  const timeButtonClass = (range: TimeRange) =>
    cx(
      'py-1.5 px-3 rounded text-sm font-medium transition-colors',
      timeRange === range
        ? 'bg-primary text-white'
        : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-50'
    )

  const refreshButtonClass = (interval: RefreshInterval) =>
    cx(
      'py-1.5 px-2 rounded text-sm font-medium transition-colors',
      refreshInterval === interval
        ? 'bg-primary text-white'
        : 'bg-white text-slate-600 border border-slate-200 hover:bg-slate-50'
    )

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <header className="flex flex-wrap items-center gap-4 mb-6 pb-4 border-b border-slate-200">
        <div className="flex items-center gap-4 flex-1">
          <Link to="/ui" className="text-primary hover:underline">&larr; Dashboard</Link>
          <h1 className="text-2xl font-semibold text-slate-800">Docker Logs</h1>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <div className="flex gap-1">
            {TIME_RANGES.map(range => (
              <button
                key={range.value}
                className={timeButtonClass(range.value)}
                onClick={() => setTimeRange(range.value)}
              >
                {range.label}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-1">
            <span className="text-sm text-slate-500">Auto:</span>
            {REFRESH_INTERVALS.map(interval => (
              <button
                key={interval.value}
                className={refreshButtonClass(interval.value)}
                onClick={() => setRefreshInterval(interval.value)}
              >
                {interval.label}
              </button>
            ))}
          </div>

          <button
            onClick={loadLogs}
            className={styles.btnPrimary}
            disabled={loading}
          >
            {loading ? 'Loading...' : 'Refresh'}
          </button>
        </div>
      </header>

      {error && (
        <div className={cx(styles.errorBanner, 'flex justify-between items-center mb-6')}>
          <p>{error}</p>
          <button onClick={loadLogs} className="text-primary hover:underline">Retry</button>
        </div>
      )}

      <div className="bg-white rounded-xl shadow-md overflow-hidden">
        <div className="flex border-b border-slate-200">
          {containers.map(container => (
            <button
              key={container.name}
              className={tabClass(container.name)}
              onClick={() => setSelectedContainer(container.name)}
            >
              <span>{getDisplayName(container.name)}</span>
              <span className={cx(
                'ml-2 px-1.5 py-0.5 rounded text-xs',
                container.status.toLowerCase().includes('up')
                  ? 'bg-green-100 text-green-700'
                  : 'bg-slate-100 text-slate-500'
              )}>
                {container.status}
              </span>
            </button>
          ))}
        </div>

        <div className="p-4 border-b border-slate-200 flex gap-4 items-center">
          <input
            type="text"
            placeholder="Filter logs..."
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
            className={cx(styles.formInput, 'flex-1 max-w-md')}
          />

          <label className="flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
              className="rounded border-slate-300"
            />
            Auto-scroll
          </label>

          {logs && (
            <span className="text-sm text-slate-500">
              {logs.lines.toLocaleString()} lines
            </span>
          )}
        </div>

        <pre
          ref={logViewerRef}
          className="bg-slate-900 text-slate-100 p-4 font-mono text-sm overflow-auto h-[600px] whitespace-pre-wrap"
        >
          {loading && !logs ? (
            <span className="text-slate-500">Loading logs...</span>
          ) : logs?.logs ? (
            logs.logs.split('\n').map((line, i) => (
              <LogLine key={i} line={line} filterText={filterText} />
            ))
          ) : (
            <span className="text-slate-500">No logs available</span>
          )}
        </pre>
      </div>
    </div>
  )
}

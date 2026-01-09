import React from 'react'

interface SummaryCardsProps {
  totalEvents: number
  successRate: number
  avgDuration: number
  systemMetrics: Record<string, number>
}

export const SummaryCards: React.FC<SummaryCardsProps> = ({
  totalEvents,
  successRate,
  avgDuration,
  systemMetrics,
}) => {
  const hasSystemCpu = 'system.cpu_percent' in systemMetrics
  const cpuPercent = systemMetrics['system.cpu_percent'] ?? systemMetrics['process.cpu_percent']
  const memoryPercent = systemMetrics['system.memory_percent']
  const diskPercent = systemMetrics['system.disk_usage_percent']

  return (
    <div className="summary-cards">
      <div className="summary-card">
        <span className="card-value">{formatNumber(totalEvents)}</span>
        <span className="card-label">Total Events</span>
      </div>

      <div className="summary-card">
        <span className={`card-value ${getSuccessRateColor(successRate)}`}>
          {successRate}%
        </span>
        <span className="card-label">Success Rate</span>
      </div>

      <div className="summary-card">
        <span className="card-value">{formatDuration(avgDuration)}</span>
        <span className="card-label">Avg Duration</span>
      </div>

      {cpuPercent !== undefined && (
        <div className="summary-card">
          <span className={`card-value ${getUsageColor(cpuPercent)}`}>
            {cpuPercent.toFixed(1)}%
          </span>
          <span className="card-label">{hasSystemCpu ? 'System CPU' : 'Process CPU'}</span>
        </div>
      )}

      {memoryPercent !== undefined && (
        <div className="summary-card">
          <span className={`card-value ${getUsageColor(memoryPercent)}`}>
            {memoryPercent.toFixed(1)}%
          </span>
          <span className="card-label">Memory Usage</span>
        </div>
      )}

      {diskPercent !== undefined && (
        <div className="summary-card">
          <span className={`card-value ${getUsageColor(diskPercent)}`}>
            {diskPercent.toFixed(1)}%
          </span>
          <span className="card-label">Disk Usage</span>
        </div>
      )}
    </div>
  )
}

// Helper functions
const formatNumber = (num: number): string => {
  if (num >= 1000000) return `${(num / 1000000).toFixed(1)}M`
  if (num >= 1000) return `${(num / 1000).toFixed(1)}K`
  return num.toString()
}

const formatDuration = (ms: number): string => {
  if (ms === 0) return '-'
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}m`
}

const getSuccessRateColor = (rate: number): string => {
  if (rate >= 95) return 'color-success'
  if (rate >= 80) return 'color-warning'
  return 'color-error'
}

const getUsageColor = (percent: number): string => {
  if (percent <= 60) return 'color-success'
  if (percent <= 80) return 'color-warning'
  return 'color-error'
}

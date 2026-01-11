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
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
      <div className="bg-white p-4 rounded-xl shadow-md text-center">
        <span className="block text-2xl font-bold text-slate-800">{formatNumber(totalEvents)}</span>
        <span className="text-sm text-slate-500">Total Events</span>
      </div>

      <div className="bg-white p-4 rounded-xl shadow-md text-center">
        <span className={`block text-2xl font-bold ${getSuccessRateColor(successRate)}`}>
          {successRate}%
        </span>
        <span className="text-sm text-slate-500">Success Rate</span>
      </div>

      <div className="bg-white p-4 rounded-xl shadow-md text-center">
        <span className="block text-2xl font-bold text-slate-800">{formatDuration(avgDuration)}</span>
        <span className="text-sm text-slate-500">Avg Duration</span>
      </div>

      {cpuPercent !== undefined && (
        <div className="bg-white p-4 rounded-xl shadow-md text-center">
          <span className={`block text-2xl font-bold ${getUsageColor(cpuPercent)}`}>
            {cpuPercent.toFixed(1)}%
          </span>
          <span className="text-sm text-slate-500">{hasSystemCpu ? 'System CPU' : 'Process CPU'}</span>
        </div>
      )}

      {memoryPercent !== undefined && (
        <div className="bg-white p-4 rounded-xl shadow-md text-center">
          <span className={`block text-2xl font-bold ${getUsageColor(memoryPercent)}`}>
            {memoryPercent.toFixed(1)}%
          </span>
          <span className="text-sm text-slate-500">Memory Usage</span>
        </div>
      )}

      {diskPercent !== undefined && (
        <div className="bg-white p-4 rounded-xl shadow-md text-center">
          <span className={`block text-2xl font-bold ${getUsageColor(diskPercent)}`}>
            {diskPercent.toFixed(1)}%
          </span>
          <span className="text-sm text-slate-500">Disk Usage</span>
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
  if (rate >= 95) return 'text-success'
  if (rate >= 80) return 'text-warning'
  return 'text-danger'
}

const getUsageColor = (percent: number): string => {
  if (percent <= 60) return 'text-success'
  if (percent <= 80) return 'text-warning'
  return 'text-danger'
}

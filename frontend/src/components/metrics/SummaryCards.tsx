import React from 'react'
import { Stat, StatGrid } from '@/components/shared/ui'

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
  const memoryAvailableMb = systemMetrics['system.memory_available_mb']
  const memoryTotalMb = systemMetrics['system.memory_total_mb']
  const diskPercent = systemMetrics['system.disk_usage_percent']
  const diskFreeGb = systemMetrics['system.disk_free_gb']
  const diskTotalGb = systemMetrics['system.disk_total_gb']

  const memoryDetail = formatUsedTotal(memoryTotalMb, memoryAvailableMb, mbToHuman)
  const diskDetail = formatUsedTotal(diskTotalGb, diskFreeGb, gbToHuman)

  return (
    <StatGrid>
      <Stat label="Total events" value={formatNumber(totalEvents)} />
      <Stat label="Success rate" value={`${successRate}%`} tone={successRateTone(successRate)} />
      <Stat label="Avg duration" value={formatDuration(avgDuration)} />
      {cpuPercent !== undefined && (
        <Stat label={hasSystemCpu ? 'System CPU' : 'Process CPU'} value={`${cpuPercent.toFixed(1)}%`} tone={usageTone(cpuPercent)} />
      )}
      {memoryPercent !== undefined && (
        <Stat label="Memory usage" value={`${memoryPercent.toFixed(1)}%`} tone={usageTone(memoryPercent)} hint={memoryDetail} />
      )}
      {diskPercent !== undefined && (
        <Stat label="Disk usage" value={`${diskPercent.toFixed(1)}%`} tone={usageTone(diskPercent)} hint={diskDetail} />
      )}
    </StatGrid>
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

const successRateTone = (rate: number) => rate >= 95 ? 'success' : rate >= 80 ? 'warning' : 'danger'

const usageTone = (percent: number) => percent <= 60 ? 'success' : percent <= 80 ? 'warning' : 'danger'

// Format "used / total" given total and free/available, using a unit-aware formatter.
const formatUsedTotal = (
  total: number | undefined,
  free: number | undefined,
  format: (value: number) => string,
): string | null => {
  if (total === undefined || free === undefined) return null
  const used = Math.max(total - free, 0)
  return `${format(used)} / ${format(total)}`
}

const mbToHuman = (mb: number): string => {
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`
  return `${mb.toFixed(0)} MB`
}

const gbToHuman = (gb: number): string => {
  if (gb >= 1024) return `${(gb / 1024).toFixed(1)} TB`
  if (gb >= 1) return `${gb.toFixed(1)} GB`
  return `${(gb * 1024).toFixed(0)} MB`
}

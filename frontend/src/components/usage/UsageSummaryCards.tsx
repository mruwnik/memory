import React from 'react'
import { Stat, StatGrid } from '@/components/shared/ui'
import { formatCost } from '@/utils/format'
import { formatTokens } from './UsageChart'

interface UsageSummaryCardsProps {
  totalCost: number
  totalTokens: number
  /** Null until Claude Code telemetry has loaded. */
  totalSessions: number | null
  eventCount: number | null
}

export const UsageSummaryCards: React.FC<UsageSummaryCardsProps> = ({
  totalCost,
  totalTokens,
  totalSessions,
  eventCount,
}) => (
  <StatGrid>
    <Stat label="Total cost" value={formatCost(totalCost)} hint="All sources" />
    <Stat label="Total tokens" value={formatTokens(totalTokens)} hint="Input, output and cache" />
    <Stat label="Sessions" value={totalSessions?.toString() ?? '—'} hint="Claude Code" />
    <Stat label="Events" value={eventCount?.toString() ?? '—'} hint="Claude Code" />
  </StatGrid>
)

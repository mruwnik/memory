import React from 'react'
import { Stat, StatGrid } from '@/components/shared/ui'
import { formatCost } from '@/utils/format'

interface TelemetrySummaryCardsProps {
  totalTokens: number
  totalCost: number
  totalSessions: number
  eventCount: number
}

export const TelemetrySummaryCards: React.FC<TelemetrySummaryCardsProps> = ({
  totalTokens,
  totalCost,
  totalSessions,
  eventCount,
}) => {
  const formatTokens = (tokens: number): string => {
    if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`
    if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}K`
    return tokens.toString()
  }

  return (
    <StatGrid>
      <Stat label="Total tokens" value={formatTokens(totalTokens)} hint="Input + Output" />
      <Stat label="Total cost" value={formatCost(totalCost)} />
      <Stat label="Sessions" value={totalSessions.toString()} />
      <Stat label="Events" value={eventCount.toString()} />
    </StatGrid>
  )
}

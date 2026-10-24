import React from 'react'
import { Stat, StatGrid } from '@/components/shared/ui'

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

  const formatCost = (cost: number): string => {
    return `$${cost.toFixed(4)}`
  }

  return (
    <StatGrid>
      <Stat label="Total Tokens" value={formatTokens(totalTokens)} hint="Input + Output" />
      <Stat label="Total Cost" value={formatCost(totalCost)} hint="API usage" />
      <Stat label="Sessions" value={totalSessions.toString()} hint="Active sessions" />
      <Stat label="Events" value={eventCount.toString()} hint="Total events" />
    </StatGrid>
  )
}

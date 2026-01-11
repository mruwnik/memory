import React from 'react'

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
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      <SummaryCard
        title="Total Tokens"
        value={formatTokens(totalTokens)}
        subtitle="Input + Output"
        color="blue"
      />
      <SummaryCard
        title="Total Cost"
        value={formatCost(totalCost)}
        subtitle="API usage"
        color="green"
      />
      <SummaryCard
        title="Sessions"
        value={totalSessions.toString()}
        subtitle="Active sessions"
        color="purple"
      />
      <SummaryCard
        title="Events"
        value={eventCount.toString()}
        subtitle="Total events"
        color="orange"
      />
    </div>
  )
}

interface SummaryCardProps {
  title: string
  value: string
  subtitle: string
  color: 'blue' | 'green' | 'purple' | 'orange'
}

const SummaryCard: React.FC<SummaryCardProps> = ({ title, value, subtitle, color }) => {
  const colorClasses = {
    blue: 'bg-blue-50 border-blue-200 text-blue-700',
    green: 'bg-green-50 border-green-200 text-green-700',
    purple: 'bg-purple-50 border-purple-200 text-purple-700',
    orange: 'bg-orange-50 border-orange-200 text-orange-700',
  }

  return (
    <div className={`${colorClasses[color]} border rounded-xl p-4`}>
      <p className="text-sm font-medium opacity-80">{title}</p>
      <p className="text-2xl font-bold mt-1">{value}</p>
      <p className="text-xs opacity-60 mt-1">{subtitle}</p>
    </div>
  )
}

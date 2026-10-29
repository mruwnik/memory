import { describe, it, expect } from 'vitest'
import { render, screen } from '@/test/utils'
import { UsageSummaryCards } from './UsageSummaryCards'

describe('UsageSummaryCards', () => {
  it('renders all four card titles', () => {
    render(
      <UsageSummaryCards totalTokens={0} totalCost={0} totalSessions={0} eventCount={0} />,
    )
    expect(screen.getByText('Total tokens')).toBeInTheDocument()
    expect(screen.getByText('Total cost')).toBeInTheDocument()
    expect(screen.getByText('Sessions')).toBeInTheDocument()
    expect(screen.getByText('Events')).toBeInTheDocument()
    expect(screen.getByText('All sources')).toBeInTheDocument()
  })

  it.each([
    [0, '0'],
    [999, '999'],
    [1000, '1.0K'],
    [1500, '1.5K'],
    [999999, '1000.0K'],
    [1_000_000, '1.0M'],
    [2_500_000, '2.5M'],
  ])('formats %i tokens as %s', (tokens, expected) => {
    render(
      <UsageSummaryCards
        totalTokens={tokens}
        totalCost={0}
        totalSessions={123456}
        eventCount={789012}
      />,
    )
    expect(screen.getByText(expected)).toBeInTheDocument()
  })

  it.each([
    [0, '$0.00'],
    [0.004, '<$0.01'],
    [1.23456, '$1.23'],
    [12, '$12.00'],
  ])('formats cost %f as %s', (cost, expected) => {
    render(
      <UsageSummaryCards totalTokens={0} totalCost={cost} totalSessions={0} eventCount={0} />,
    )
    expect(screen.getByText(expected)).toBeInTheDocument()
  })

  it('renders session and event counts as plain integers', () => {
    render(
      <UsageSummaryCards
        totalTokens={0}
        totalCost={0}
        totalSessions={42}
        eventCount={137}
      />,
    )
    expect(screen.getByText('42')).toBeInTheDocument()
    expect(screen.getByText('137')).toBeInTheDocument()
  })
})

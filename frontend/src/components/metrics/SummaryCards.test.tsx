import { describe, it, expect } from 'vitest'
import { render, screen } from '@/test/utils'
import { SummaryCards } from './SummaryCards'

const baseProps = {
  totalEvents: 0,
  successRate: 0,
  avgDuration: 0,
  systemMetrics: {} as Record<string, number>,
}

describe('SummaryCards', () => {
  it('always renders the three core cards', () => {
    render(<SummaryCards {...baseProps} />)
    expect(screen.getByText('Total Events')).toBeInTheDocument()
    expect(screen.getByText('Success Rate')).toBeInTheDocument()
    expect(screen.getByText('Avg Duration')).toBeInTheDocument()
  })

  it.each([
    [0, '0'],
    [999, '999'],
    [1000, '1.0K'],
    [1_500_000, '1.5M'],
  ])('formats total events %i as %s', (events, expected) => {
    render(<SummaryCards {...baseProps} totalEvents={events} />)
    expect(screen.getByText(expected)).toBeInTheDocument()
  })

  it.each([
    [0, '-'],
    [500, '500ms'],
    [1500, '1.5s'],
    [90000, '1.5m'],
  ])('formats avg duration %i as %s', (ms, expected) => {
    render(<SummaryCards {...baseProps} avgDuration={ms} />)
    expect(screen.getByText(expected)).toBeInTheDocument()
  })

  it('renders the success rate as a percentage', () => {
    render(<SummaryCards {...baseProps} successRate={87} />)
    expect(screen.getByText('87%')).toBeInTheDocument()
  })

  it('omits the CPU, memory and disk cards when no system metrics present', () => {
    render(<SummaryCards {...baseProps} />)
    expect(screen.queryByText('System CPU')).not.toBeInTheDocument()
    expect(screen.queryByText('Process CPU')).not.toBeInTheDocument()
    expect(screen.queryByText('Memory Usage')).not.toBeInTheDocument()
    expect(screen.queryByText('Disk Usage')).not.toBeInTheDocument()
  })

  it('labels CPU card "System CPU" when system.cpu_percent present', () => {
    render(<SummaryCards {...baseProps} systemMetrics={{ 'system.cpu_percent': 42.25 }} />)
    expect(screen.getByText('System CPU')).toBeInTheDocument()
    expect(screen.getByText('42.3%')).toBeInTheDocument()
  })

  it('falls back to process CPU when only process.cpu_percent is present', () => {
    render(<SummaryCards {...baseProps} systemMetrics={{ 'process.cpu_percent': 12.0 }} />)
    expect(screen.getByText('Process CPU')).toBeInTheDocument()
    expect(screen.getByText('12.0%')).toBeInTheDocument()
  })

  it('shows memory usage with a used / total detail line', () => {
    render(
      <SummaryCards
        {...baseProps}
        systemMetrics={{
          'system.memory_percent': 75.0,
          'system.memory_total_mb': 2048,
          'system.memory_available_mb': 512,
        }}
      />,
    )
    expect(screen.getByText('Memory Usage')).toBeInTheDocument()
    expect(screen.getByText('75.0%')).toBeInTheDocument()
    // used = 2048-512 = 1536 MB -> 1.5 GB ; total 2048 MB -> 2.0 GB
    expect(screen.getByText('1.5 GB / 2.0 GB')).toBeInTheDocument()
  })

  it('shows disk usage with a used / total detail line', () => {
    render(
      <SummaryCards
        {...baseProps}
        systemMetrics={{
          'system.disk_usage_percent': 50.0,
          'system.disk_total_gb': 100,
          'system.disk_free_gb': 40,
        }}
      />,
    )
    expect(screen.getByText('Disk Usage')).toBeInTheDocument()
    // used = 100-40 = 60 GB ; total 100 GB
    expect(screen.getByText('60.0 GB / 100.0 GB')).toBeInTheDocument()
  })

  it('omits the memory detail line when total/available are missing', () => {
    render(<SummaryCards {...baseProps} systemMetrics={{ 'system.memory_percent': 30 }} />)
    expect(screen.getByText('Memory Usage')).toBeInTheDocument()
    expect(screen.queryByText(/\/ /)).not.toBeInTheDocument()
  })
})

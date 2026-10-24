import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, renderWithRouter, screen, waitFor } from '@/test/utils'
import type { FleetStats, Environment } from '@/hooks/useClaude'
import ClaudeFleetStats, {
  parseSessionId,
  sessionDisplay,
  HistoryChart,
  Usage,
} from './ClaudeFleetStats'

const getFleetStats = vi.fn()
const getStatsHistory = vi.fn()
const listUsers = vi.fn()

vi.mock('@/hooks/useClaude', async (orig) => {
  const actual = await orig<typeof import('@/hooks/useClaude')>()
  return { ...actual, useClaude: () => ({ getFleetStats, getStatsHistory }) }
})
vi.mock('@/hooks/useUsers', () => ({ useUsers: () => ({ listUsers }) }))

const env = (id: number, name: string): Environment => ({
  id, name, volume_name: `vol${id}`, description: null,
  initialized_from_snapshot_id: null, cloned_from_environment_id: null,
  size_bytes: null, last_used_at: null, created_at: null, session_count: 0,
})

const snap = (over: Partial<FleetStats> = {}): FleetStats => ({
  ts: new Date().toISOString(),
  global: { running: 2, max: 5, memory_mb: { used: 1024, allocated: 2048, max: 8192 }, cpus: { used: 1.5, allocated: 3, max: 8 } },
  containers: [
    { id: 'u1-e3-abc', status: 'running', allocated: { memory_mb: 2048, cpus: 2 }, used: { memory_mb: 512, memory_pct: 25, cpu_pct: 40 } },
    { id: 'u2-s7-def', status: 'exited', allocated: { memory_mb: 1024, cpus: 1 }, used: null },
  ],
  ...over,
})

beforeEach(() => {
  getFleetStats.mockReset().mockResolvedValue(snap())
  getStatsHistory.mockReset().mockResolvedValue({ points: [], count: 0, truncated: false })
  listUsers.mockReset().mockResolvedValue([])
})

describe('parseSessionId', () => {
  it.each([
    ['u1-e3-abc', { userId: 1, sourceType: 'environment', sourceId: 3, hex: 'abc' }],
    ['u2-s7-def', { userId: 2, sourceType: 'snapshot', sourceId: 7, hex: 'def' }],
    ['u5-x-zzz', { userId: 5, sourceType: 'unknown', sourceId: null, hex: 'zzz' }],
  ])('parses %s', (id, expected) => {
    expect(parseSessionId(id)).toEqual(expected)
  })

  it.each(['notvalid', 'a-b', 'x1-e3-abc', 'unum-e3-abc'])('returns null for %s', (id) => {
    expect(parseSessionId(id)).toBeNull()
  })
})

describe('sessionDisplay', () => {
  it('uses the environment name when known', () => {
    expect(sessionDisplay('u1-e3-abc', [env(3, 'prod-env')], [{ id: 1, name: 'Ada' } as never]))
      .toEqual({ title: 'prod-env', subtitle: 'Ada' })
  })
  it('falls back to env #N and user N', () => {
    expect(sessionDisplay('u9-e4-abc', [], [])).toEqual({ title: 'env #4', subtitle: 'user 9' })
  })
  it('labels snapshots', () => {
    expect(sessionDisplay('u1-s7-abc', [], []).title).toBe('snapshot #7')
  })
  it('returns the raw id when unparseable', () => {
    expect(sessionDisplay('garbage', [], [])).toEqual({ title: 'garbage', subtitle: null })
  })
})

describe('Usage', () => {
  it.each([
    [50, '50%'],
    [80, '80% (high)'],
    [95, '95% (critical)'],
  ])('labels %i%% usage as %s, not only by colour', (pct, text) => {
    const { container } = render(<Usage pct={pct}>{pct}%</Usage>)
    expect(container).toHaveTextContent(text)
  })
})

describe('HistoryChart', () => {
  it('shows an empty message with no points', () => {
    render(<HistoryChart points={[]} />)
    expect(screen.getByText(/No history yet/)).toBeInTheDocument()
  })
  it('renders chart sections when points exist', () => {
    render(<HistoryChart points={[{ ts: new Date().toISOString(), session_id: 'u1-e3-x', cpu_pct: 10, memory_mb: 100, memory_pct: 5 }]} />)
    expect(screen.getByText('CPU (% of one core)')).toBeInTheDocument()
    expect(screen.getByText('Memory (MB)')).toBeInTheDocument()
  })
})

describe('ClaudeFleetStats', () => {
  const baseProps = {
    environments: [env(3, 'prod-env')],
    hasAdminScope: false,
    currentUserId: 1,
  }

  it('shows a loading message before the first snapshot arrives', () => {
    getFleetStats.mockReturnValue(new Promise(() => {}))
    renderWithRouter(<ClaudeFleetStats {...baseProps} />)
    expect(screen.getByText(/Loading fleet stats/)).toBeInTheDocument()
  })

  it('renders an error when stats fail', async () => {
    getFleetStats.mockRejectedValue(new Error('stats down'))
    renderWithRouter(<ClaudeFleetStats {...baseProps} />)
    await waitFor(() => expect(screen.getByText('stats down')).toBeInTheDocument())
  })

  it('renders the global cluster summary and container rows', async () => {
    renderWithRouter(<ClaudeFleetStats {...baseProps} />)
    await waitFor(() => expect(screen.getByText('Cluster')).toBeInTheDocument())
    expect(screen.getByText('2 / 5 containers')).toBeInTheDocument()
    expect(screen.getByText('Containers')).toBeInTheDocument()
    expect(screen.getByText('prod-env')).toBeInTheDocument()
  })

  it('shows an empty message when no containers are running', async () => {
    getFleetStats.mockResolvedValue(snap({ containers: [] }))
    renderWithRouter(<ClaudeFleetStats {...baseProps} />)
    await waitFor(() => expect(screen.getByText('No containers running.')).toBeInTheDocument())
  })

  it('links owned containers to their session', async () => {
    renderWithRouter(<ClaudeFleetStats {...baseProps} currentUserId={1} />)
    expect(await screen.findByRole('link', { name: 'prod-env' })).toHaveAttribute('href', '/ui/claude/u1-e3-abc')
    expect(screen.getByRole('table', { name: 'Running containers' })).toBeInTheDocument()
  })

  it("says which rows belong to other users and doesn't link them", async () => {
    renderWithRouter(<ClaudeFleetStats {...baseProps} currentUserId={1} />)
    expect(await screen.findByText('snapshot #7')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'snapshot #7' })).not.toBeInTheDocument()
    expect(screen.getByText('Not yours')).toBeInTheDocument()
  })
})

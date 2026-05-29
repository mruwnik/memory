import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@/test/utils'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import userEvent from '@testing-library/user-event'
import type { ClaudeSession, Snapshot, Environment, AttachInfo } from '@/hooks/useClaude'

// xterm doesn't run in jsdom.
vi.mock('@xterm/xterm', () => ({
  Terminal: class {
    cols = 80; rows = 24
    loadAddon = vi.fn(); open = vi.fn(); write = vi.fn(); reset = vi.fn()
    dispose = vi.fn(); onData = vi.fn()
  },
}))
vi.mock('@xterm/addon-fit', () => ({ FitAddon: class { fit = vi.fn() } }))
vi.mock('@xterm/xterm/css/xterm.css', () => ({}))

const listSessions = vi.fn()
const killSession = vi.fn()
const spawnSession = vi.fn()
const scheduleSession = vi.fn()
const getAttachInfo = vi.fn()
const getOrchestratorStatus = vi.fn()
const listSnapshots = vi.fn()
const listEnvironments = vi.fn()
const listUserRepos = vi.fn()
const getFleetStats = vi.fn()
const getStatsHistory = vi.fn()
const listUsers = vi.fn()
const hasScope = vi.fn()

vi.mock('@/hooks/useClaude', async (orig) => {
  const actual = await orig<typeof import('@/hooks/useClaude')>()
  return {
    ...actual,
    getLogStreamUrl: (id: string) => `ws://test/${id}`,
    getDifferUrl: (id: string) => `http://differ/${id}`,
    useClaude: () => ({
      listSessions, killSession, spawnSession, scheduleSession, getAttachInfo,
      getOrchestratorStatus, listSnapshots, listEnvironments, listUserRepos,
      getFleetStats, getStatsHistory,
    }),
  }
})
vi.mock('../../hooks/useClaude', async (orig) => {
  const actual = await orig<typeof import('../../hooks/useClaude')>()
  return {
    ...actual,
    getLogStreamUrl: (id: string) => `ws://test/${id}`,
    getDifferUrl: (id: string) => `http://differ/${id}`,
    useClaude: () => ({
      listSessions, killSession, spawnSession, scheduleSession, getAttachInfo,
      getOrchestratorStatus, listSnapshots, listEnvironments, listUserRepos,
      getFleetStats, getStatsHistory,
    }),
  }
})
vi.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({ hasScope, user: { id: 1, name: 'Me', email: 'me@x.com', user_type: 'human', scopes: [] } }),
}))
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ hasScope, user: { id: 1, name: 'Me', email: 'me@x.com', user_type: 'human', scopes: [] } }),
}))
vi.mock('@/hooks/useUsers', () => ({ useUsers: () => ({ listUsers }) }))
vi.mock('../../hooks/useUsers', () => ({ useUsers: () => ({ listUsers }) }))

import ClaudeSessions from './ClaudeSessions'

// Render inside real routes so useParams() resolves :sessionId.
const renderClaude = (path: string = '/ui/claude') =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/ui/claude" element={<ClaudeSessions />} />
        <Route path="/ui/claude/:sessionId" element={<ClaudeSessions />} />
      </Routes>
    </MemoryRouter>,
  )

// Minimal WebSocket stub.
class FakeWS {
  static OPEN = 1
  readyState = 1
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: ((e: { code: number; reason: string }) => void) | null = null
  send = vi.fn()
  close = vi.fn()
}

const session = (o: Partial<ClaudeSession> = {}): ClaudeSession => ({
  session_id: 'u1-e3-abc',
  container_id: 'cid',
  container_name: 'claude-abc',
  status: 'running',
  environment_id: 3,
  differ: null,
  ...o,
})

const environment: Environment = {
  id: 3, name: 'prod-env', volume_name: 'v3', description: null,
  initialized_from_snapshot_id: null, cloned_from_environment_id: null,
  size_bytes: null, last_used_at: null, created_at: null, session_count: 0,
}

const snapshot: Snapshot = {
  id: 7, name: 'base-snap', content_hash: 'h', claude_account_email: null,
  subscription_type: 'pro', summary: null, filename: 'f', size: 1, created_at: null,
}

const attach: AttachInfo = {
  session_id: 'u1-e3-abc', container_name: 'claude-abc',
  attach_cmd: 'docker attach claude-abc', exec_cmd: 'docker exec -it claude-abc bash',
}

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('WebSocket', FakeWS as unknown as typeof WebSocket)
  vi.stubGlobal('confirm', vi.fn(() => true))
  listSessions.mockReset().mockResolvedValue([])
  killSession.mockReset().mockResolvedValue(undefined)
  spawnSession.mockReset().mockResolvedValue(session({ session_id: 'u1-e3-new' }))
  scheduleSession.mockReset().mockResolvedValue({ task_id: 'task-1', cron_expression: '0 9 * * *', next_scheduled_time: new Date().toISOString(), topic: 't' })
  getAttachInfo.mockReset().mockResolvedValue(attach)
  getOrchestratorStatus.mockReset().mockResolvedValue({ available: true, socket_path: null })
  listSnapshots.mockReset().mockResolvedValue([snapshot])
  listEnvironments.mockReset().mockResolvedValue([environment])
  listUserRepos.mockReset().mockResolvedValue([{ id: 1, owner: 'org', name: 'repo', repo_path: 'org/repo' }])
  getFleetStats.mockReset().mockResolvedValue({ ts: new Date().toISOString(), containers: [] })
  getStatsHistory.mockReset().mockResolvedValue({ points: [], count: 0, truncated: false })
  listUsers.mockReset().mockResolvedValue([])
  hasScope.mockReset().mockReturnValue(false)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ClaudeSessions', () => {
  it('shows a loading state then the fleet overview empty state', async () => {
    renderClaude('/ui/claude')
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Fleet Overview')).toBeInTheDocument())
    expect(screen.getByText('No active sessions')).toBeInTheDocument()
  })

  it('surfaces a load error', async () => {
    listEnvironments.mockRejectedValue(new Error('orch down'))
    renderClaude('/ui/claude')
    await waitFor(() => expect(screen.getByText('orch down')).toBeInTheDocument())
  })

  it('shows the orchestrator-offline badge', async () => {
    getOrchestratorStatus.mockResolvedValue({ available: false, socket_path: null })
    renderClaude('/ui/claude')
    await waitFor(() => expect(screen.getByText('Orchestrator Offline')).toBeInTheDocument())
  })

  it('lists active sessions in the sidebar', async () => {
    listSessions.mockResolvedValue([session({ session_id: 'u1-e3-abc', status: 'running' })])
    renderClaude('/ui/claude')
    await waitFor(() => expect(screen.getByText('u1-e3-abc')).toBeInTheDocument())
    expect(screen.getByText('running')).toBeInTheDocument()
  })

  it('opens the new-session form and shows config options', async () => {
    const user = userEvent.setup()
    renderClaude('/ui/claude')
    await waitFor(() => expect(screen.getByText('Fleet Overview')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: '+ New Session' }))
    expect(screen.getByText('Start New Session')).toBeInTheDocument()
    expect(screen.getByText('Configuration *')).toBeInTheDocument()
  })

  it('warns when there are no snapshots or environments', async () => {
    listSnapshots.mockResolvedValue([])
    listEnvironments.mockResolvedValue([])
    const user = userEvent.setup()
    renderClaude('/ui/claude')
    await waitFor(() => expect(screen.getByText('Fleet Overview')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: '+ New Session' }))
    expect(screen.getByText('No snapshots or environments available')).toBeInTheDocument()
  })

  it('spawns a session from the form', async () => {
    const user = userEvent.setup()
    renderClaude('/ui/claude')
    await waitFor(() => expect(screen.getByText('Fleet Overview')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: '+ New Session' }))
    await user.click(screen.getByRole('button', { name: 'Start Session' }))
    await waitFor(() => expect(spawnSession).toHaveBeenCalled())
    expect(spawnSession.mock.calls[0][0]).toMatchObject({ environment_id: 3 })
  })

  it('requires an initial prompt to schedule a recurring session', async () => {
    const user = userEvent.setup()
    renderClaude('/ui/claude')
    await waitFor(() => expect(screen.getByText('Fleet Overview')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: '+ New Session' }))
    await user.type(screen.getByPlaceholderText('0 9 * * *'), '0 9 * * *')
    expect(screen.getByText('An initial prompt is required for scheduled sessions.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Schedule Recurring Session' }))
    expect(screen.getByText('Scheduled sessions require an initial prompt')).toBeInTheDocument()
    expect(scheduleSession).not.toHaveBeenCalled()
  })

  it('schedules a recurring session when a prompt and cron are set', async () => {
    const user = userEvent.setup()
    renderClaude('/ui/claude')
    await waitFor(() => expect(screen.getByText('Fleet Overview')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: '+ New Session' }))
    await user.type(screen.getByPlaceholderText('Start Claude with this prompt...'), 'do work')
    await user.click(screen.getByRole('button', { name: 'Daily 9am' }))
    await user.click(screen.getByRole('button', { name: 'Schedule Recurring Session' }))
    await waitFor(() => expect(scheduleSession).toHaveBeenCalled())
    expect(screen.getByText('Schedule created')).toBeInTheDocument()
  })

  it('toggles pre-approved tools and persists to localStorage', async () => {
    const user = userEvent.setup()
    renderClaude('/ui/claude')
    await waitFor(() => expect(screen.getByText('Fleet Overview')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: '+ New Session' }))
    const bashToggle = screen.getByRole('checkbox', { name: 'Bash' })
    expect(bashToggle).toBeChecked()
    await user.click(bashToggle)
    await waitFor(() => {
      const saved = JSON.parse(localStorage.getItem('claude_session_allowed_tools') || '[]')
      expect(saved).not.toContain('Bash')
    })
  })

  it('selecting a session via the URL shows its details and connects the websocket', async () => {
    listSessions.mockResolvedValue([session({ session_id: 'u1-e3-abc' })])
    renderClaude('/ui/claude/u1-e3-abc')
    await waitFor(() => expect(getAttachInfo).toHaveBeenCalledWith('u1-e3-abc'))
    await waitFor(() => expect(screen.getByText('Session ID')).toBeInTheDocument())
    expect(screen.getByText('claude-abc')).toBeInTheDocument()
  })

  it('kills a session after confirmation', async () => {
    listSessions.mockResolvedValue([session({ session_id: 'u1-e3-abc' })])
    const user = userEvent.setup()
    renderClaude('/ui/claude')
    await waitFor(() => expect(screen.getByText('u1-e3-abc')).toBeInTheDocument())
    await user.click(screen.getByTitle('Kill session'))
    expect(confirm).toHaveBeenCalled()
    await waitFor(() => expect(killSession).toHaveBeenCalledWith('u1-e3-abc'))
  })

  it('dismisses the error banner', async () => {
    listEnvironments.mockRejectedValue(new Error('boom err'))
    const user = userEvent.setup()
    renderClaude('/ui/claude')
    await waitFor(() => expect(screen.getByText('boom err')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByText('boom err')).not.toBeInTheDocument()
  })
})

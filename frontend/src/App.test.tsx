import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, userEvent } from '@/test/utils'

// --- Hook mocks (drive auth/oauth state) ---
const authState = {
  isAuthenticated: false,
  isLoading: false,
  logout: vi.fn(),
  checkAuth: vi.fn(async () => true),
  user: { id: 1, name: 'Ada', email: 'a@b.c', user_type: 'human', scopes: ['*'] },
  hasScope: vi.fn(() => true),
}
const oauthState = {
  error: null as string | null,
  startOAuth: vi.fn(),
  handleCallback: vi.fn(async () => true),
  clearError: vi.fn(),
}

vi.mock('@/hooks/useAuth', () => ({ useAuth: () => authState }))
vi.mock('@/hooks/useOAuth', () => ({ useOAuth: () => oauthState }))

// Stub the heavy / unrelated screens so we only assert routing.
vi.mock('@/components', () => ({
  Loading: () => <div>LOADING</div>,
  LoginPrompt: ({ onLogin }: { onLogin: () => void }) => (
    <button onClick={onLogin}>LOGIN_PROMPT</button>
  ),
  AuthError: ({ error, onRetry }: { error: string; onRetry: () => void }) => (
    <div>
      <span>AUTH_ERROR:{error}</span>
      <button onClick={onRetry}>RETRY</button>
    </div>
  ),
  Dashboard: () => <div>DASHBOARD</div>,
  Search: () => <div>SEARCH</div>,
  Sources: () => <div>SOURCES</div>,
  Calendar: () => <div>CALENDAR</div>,
  Tasks: () => <div>TASKS</div>,
  NotesPage: () => <div>NOTES</div>,
  Jobs: () => <div>JOBS</div>,
  DockerLogs: () => <div>LOGS</div>,
  ConfigSources: () => <div>SNAPSHOTS</div>,
  CeleryOverview: () => <div>CELERY</div>,
  ScheduledTasks: () => <div>SCHEDULED</div>,
  ReportsPage: () => <div>REPORTS</div>,
}))
vi.mock('@/components/polls', () => ({
  PollList: () => <div>POLL_LIST</div>,
  PollCreate: () => <div>POLL_CREATE</div>,
  PollEdit: () => <div>POLL_EDIT</div>,
  PollRespond: () => <div>POLL_RESPOND</div>,
  PollResults: () => <div>POLL_RESULTS</div>,
}))
vi.mock('@/components/users', () => ({
  UserSettings: () => <div>SETTINGS</div>,
  UserManagement: () => <div>USER_MGMT</div>,
}))
vi.mock('@/components/people', () => ({ PeopleManagement: () => <div>PEOPLE</div> }))
vi.mock('@/components/projects/Projects', () => ({ default: () => <div>PROJECTS</div> }))
vi.mock('@/components/teams/Teams', () => ({ default: () => <div>TEAMS</div> }))
vi.mock('@/components/metrics/Metrics', () => ({ default: () => <div>METRICS</div> }))
vi.mock('@/components/telemetry/Telemetry', () => ({ default: () => <div>TELEMETRY</div> }))
vi.mock('@/components/claude/ClaudeSessions', () => ({ default: () => <div>CLAUDE</div> }))

import App from './App'

const setPath = (path: string) => window.history.pushState({}, '', path)

beforeEach(() => {
  authState.isAuthenticated = false
  authState.isLoading = false
  authState.hasScope = vi.fn(() => true)
  authState.logout = vi.fn()
  authState.checkAuth = vi.fn(async () => true)
  oauthState.error = null
  oauthState.startOAuth = vi.fn()
  oauthState.handleCallback = vi.fn(async () => true)
  oauthState.clearError = vi.fn()
  setPath('/ui/login')
})

describe('App routing & auth', () => {
  it('shows Loading while auth resolves', () => {
    authState.isLoading = true
    render(<App />)
    expect(screen.getByText('LOADING')).toBeInTheDocument()
  })

  it('shows the AuthError screen when OAuth errored', () => {
    oauthState.error = 'boom'
    render(<App />)
    expect(screen.getByText('AUTH_ERROR:boom')).toBeInTheDocument()
  })

  it('retrying an auth error clears and restarts OAuth', async () => {
    oauthState.error = 'boom'
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByText('RETRY'))
    expect(oauthState.clearError).toHaveBeenCalled()
    expect(oauthState.startOAuth).toHaveBeenCalled()
  })

  it('renders the login prompt when unauthenticated on /ui/login', () => {
    setPath('/ui/login')
    render(<App />)
    expect(screen.getByText('LOGIN_PROMPT')).toBeInTheDocument()
  })

  it('redirects unauthenticated users from a protected route to login', async () => {
    setPath('/ui/dashboard')
    render(<App />)
    await waitFor(() => expect(screen.getByText('LOGIN_PROMPT')).toBeInTheDocument())
    expect(screen.queryByText('DASHBOARD')).not.toBeInTheDocument()
  })

  it('renders the dashboard for an authenticated user', () => {
    authState.isAuthenticated = true
    setPath('/ui/dashboard')
    render(<App />)
    expect(screen.getByText('DASHBOARD')).toBeInTheDocument()
  })

  it('redirects an authenticated user from login to dashboard', async () => {
    authState.isAuthenticated = true
    setPath('/ui/login')
    render(<App />)
    await waitFor(() => expect(screen.getByText('DASHBOARD')).toBeInTheDocument())
  })

  it('logs out and redirects when navigating to /ui/logout', async () => {
    authState.isAuthenticated = true
    setPath('/ui/logout')
    render(<App />)
    await waitFor(() => expect(authState.logout).toHaveBeenCalled())
  })

  it('allows the public poll respond route without auth', () => {
    setPath('/ui/polls/respond/my-slug')
    render(<App />)
    expect(screen.getByText('POLL_RESPOND')).toBeInTheDocument()
  })

  it('allows the public poll results route without auth', () => {
    setPath('/ui/polls/results/my-slug')
    render(<App />)
    expect(screen.getByText('POLL_RESULTS')).toBeInTheDocument()
  })

  it('gates the celery route behind the admin scope (redirects non-admin)', async () => {
    authState.isAuthenticated = true
    authState.hasScope = vi.fn((s) => s !== 'admin')
    setPath('/ui/celery')
    render(<App />)
    await waitFor(() => expect(screen.getByText('DASHBOARD')).toBeInTheDocument())
    expect(screen.queryByText('CELERY')).not.toBeInTheDocument()
  })

  it('renders celery for an admin', () => {
    authState.isAuthenticated = true
    authState.hasScope = vi.fn(() => true)
    setPath('/ui/celery')
    render(<App />)
    expect(screen.getByText('CELERY')).toBeInTheDocument()
  })

  it('lazily renders ClaudeSessions for an authenticated user', async () => {
    authState.isAuthenticated = true
    setPath('/ui/claude')
    render(<App />)
    await waitFor(() => expect(screen.getByText('CLAUDE')).toBeInTheDocument())
  })

  it('completes OAuth callback and redirects to dashboard when ?code present', async () => {
    setPath('/ui/login?code=abc123')
    render(<App />)
    await waitFor(() => expect(oauthState.handleCallback).toHaveBeenCalled())
    await waitFor(() => expect(authState.checkAuth).toHaveBeenCalled())
  })
})

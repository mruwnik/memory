import { useEffect, useMemo, useState, lazy, Suspense, type ReactNode } from 'react'
import { createBrowserRouter, RouterProvider, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom'

import { useAuth } from '@/hooks/useAuth'
import { useOAuth } from '@/hooks/useOAuth'
import { getNeedsAccount } from '@/hooks/useSetup'
import { ModulesContext, type Module } from '@/hooks/useModule'
import { Loading, LoginPrompt, AuthError, ForgotPassword, ResetPassword, Dashboard, Search, Sources, Calendar, Tasks, NotesPage, Jobs, ConfigSources, CeleryOverview, ScheduledTasks, ReportsPage } from '@/components'
import { AgentsPage, AgentEditor, AgentRuns, RunView, AdHocRun, ServersPage, ServerDetail, KeysPage } from '@/components/agents'
import { PollList, PollCreate, PollEdit, PollRespond, PollResults } from '@/components/polls'
import { UserSettings, UserManagement } from '@/components/users'
import { SettingsPage } from '@/components/settings'
import { PeopleManagement } from '@/components/people'
import { SetupWizard } from '@/components/setup/SetupWizard'
import Projects from '@/components/projects/Projects'
import Teams from '@/components/teams/Teams'
import { AppShell } from '@/components/shared/AppShell'
import { ModuleOff } from '@/components/shared/ModuleOff'
import { isVisible } from '@/components/shared/nav'
import { LoadingState } from '@/components/shared/ui'

// Lazy load heavy components (recharts ~300KB, xterm ~400KB)
const Metrics = lazy(() => import('@/components/metrics/Metrics'))
const Telemetry = lazy(() => import('@/components/telemetry/Telemetry'))
const ClaudeSessions = lazy(() => import('@/components/claude/ClaudeSessions'))

// AuthWrapper handles redirects based on auth state
const AuthWrapper = () => {
  const { isAuthenticated, isLoading, logout, checkAuth, user, hasScope } = useAuth()
  const modules = useMemo<ReadonlySet<string>>(() => new Set(user?.modules), [user])
  const { error, startOAuth, handleCallback, clearError } = useOAuth()
  const navigate = useNavigate()
  const location = useLocation()

  // Handle OAuth callback on mount
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search)
    if (urlParams.get('code')) {
      handleCallback().then(success => {
        if (success) {
          checkAuth().then(() => {
            // Redirect to dashboard after successful OAuth
            navigate('/ui/dashboard', { replace: true })
          })
        }
      })
    }
  }, [handleCallback, checkAuth, navigate])

  // Handle redirects based on auth state changes
  useEffect(() => {
    if (!isLoading) {
      if (location.pathname === '/ui/logout') {
        logout()
        navigate('/ui/login', { replace: true })
      } else if (isAuthenticated) {
        // If authenticated and on login page, redirect to dashboard
        if (location.pathname === '/ui/login' || location.pathname === '/ui') {
          navigate('/ui/dashboard', { replace: true })
        }
      } else {
        // If not authenticated and on protected route, redirect to login
        // Allow public poll routes without auth
        const isPublicRoute =
          location.pathname === '/ui/login' ||
          location.pathname === '/ui/setup' ||
          location.pathname === '/ui/forgot-password' ||
          location.pathname === '/ui/reset-password' ||
          location.pathname.startsWith('/ui/polls/respond/') ||
          location.pathname.startsWith('/ui/polls/results/')
        if (!isPublicRoute) {
          navigate('/ui/login', { replace: true })
        }
      }
    }
  }, [isAuthenticated, isLoading, location.pathname, navigate])

  // A server with no account yet has nobody to log in as, so its first visitor goes to setup.
  useEffect(() => {
    if (isLoading || isAuthenticated || location.pathname !== '/ui/login') return
    getNeedsAccount()
      .then(needed => needed && navigate('/ui/setup', { replace: true }))
      .catch(() => undefined) // The login page is already the fallback.
  }, [isAuthenticated, isLoading, location.pathname, navigate])

  // Loading state
  if (isLoading) {
    return <Loading />
  }

  // OAuth error state
  if (error) {
    return (
      <AuthError
        error={error}
        onRetry={() => {
          clearError()
          startOAuth()
        }}
      />
    )
  }

  const routes: { path: string; element: ReactNode; scope?: string; module?: Module }[] = [
    { path: '/ui/dashboard', element: <Dashboard hasScope={hasScope} /> },
    { path: '/ui/settings', element: <UserSettings /> },
    { path: '/ui/users', element: <UserManagement />, scope: 'admin:users' },
    { path: '/ui/search', element: <Search /> },
    { path: '/ui/sources', element: <Sources /> },
    { path: '/ui/calendar', element: <Calendar /> },
    { path: '/ui/tasks', element: <Tasks /> },
    { path: '/ui/metrics', element: <Suspense fallback={<LoadingState />}><Metrics /></Suspense> },
    { path: '/ui/notes', element: <NotesPage /> },
    { path: '/ui/reports', element: <ReportsPage /> },
    { path: '/ui/people', element: <PeopleManagement />, scope: 'people' },
    { path: '/ui/projects', element: <Projects />, module: 'teams' },
    { path: '/ui/teams', element: <Teams />, module: 'teams' },
    { path: '/ui/telemetry', element: <Suspense fallback={<LoadingState />}><Telemetry /></Suspense> },
    { path: '/ui/jobs', element: <Jobs /> },
    { path: '/ui/scheduled-tasks', element: <ScheduledTasks /> },
    { path: '/ui/agents', element: <AgentsPage />, scope: 'agents' },
    { path: '/ui/keys', element: <KeysPage /> },
    { path: '/ui/agents/servers', element: <ServersPage />, scope: 'agents' },
    { path: '/ui/agents/servers/:id', element: <ServerDetail />, scope: 'agents' },
    { path: '/ui/agents/runs', element: <AgentRuns />, scope: 'agents' },
    { path: '/ui/agents/runs/new', element: <AdHocRun />, scope: 'agents' },
    { path: '/ui/agents/runs/:runId', element: <RunView />, scope: 'agents' },
    { path: '/ui/agents/:id', element: <AgentEditor />, scope: 'agents' },
    { path: '/ui/snapshots', element: <ConfigSources />, module: 'claude_containers' },
    { path: '/ui/claude', element: <Suspense fallback={<LoadingState />}><ClaudeSessions /></Suspense>, scope: 'claude_sessions', module: 'claude_containers' },
    { path: '/ui/claude/:sessionId', element: <Suspense fallback={<LoadingState />}><ClaudeSessions /></Suspense>, scope: 'claude_sessions', module: 'claude_containers' },
    { path: '/ui/server-settings', element: <SettingsPage onRestarted={checkAuth} />, scope: 'admin' },
    { path: '/ui/celery', element: <CeleryOverview />, scope: 'admin' },
    { path: '/ui/polls', element: <PollList /> },
    { path: '/ui/polls/new', element: <PollCreate /> },
    { path: '/ui/polls/edit/:slug', element: <PollEdit /> },
  ]
  const home = <Navigate to={isAuthenticated ? '/ui/dashboard' : '/ui/login'} replace />
  // Results are public, but a signed-in owner reaches them from Polls and should keep the app chrome.
  const pollResults = <Route path="/ui/polls/results/:slug" element={<PollResults />} />

  return (
    <ModulesContext.Provider value={modules}>
      <Routes>
        <Route path="/ui/login" element={isAuthenticated ? home : <LoginPrompt onLogin={startOAuth} />} />
        <Route path="/ui/forgot-password" element={<ForgotPassword />} />
        <Route path="/ui/reset-password" element={<ResetPassword />} />
        <Route path="/ui/setup" element={<SetupWizard isAuthenticated={isAuthenticated} user={user} checkAuth={checkAuth} />} />

        <Route element={<AppShell user={user} onLogout={logout} hasScope={hasScope} />}>
          {routes.map(({ path, element, scope, module }) => {
            const allowed = isAuthenticated && isVisible({ scope }, hasScope, modules)
            const page = module && !modules.has(module) ? <ModuleOff path={path} module={module} isAdmin={hasScope('admin')} /> : element
            return <Route key={path} path={path} element={allowed ? page : home} />
          })}
          {isAuthenticated && pollResults}
        </Route>

        {/* Poll respond/results are public (no auth required) */}
        <Route path="/ui/polls/respond/:slug" element={<PollRespond />} />
        {!isAuthenticated && pollResults}

        <Route path="*" element={home} />
      </Routes>
    </ModulesContext.Provider>
  )
}

const appRoutes = [{ path: '*', element: <AuthWrapper /> }]

function App() {
  // Created per mount, not at import, so it reads the location current at render time.
  const [router] = useState(() => createBrowserRouter(appRoutes))
  return (
    <div className="min-h-screen flex flex-col">
      <RouterProvider router={router} />
    </div>
  )
}

export default App

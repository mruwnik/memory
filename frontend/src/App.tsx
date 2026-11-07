import { useEffect, useMemo, useState, lazy, Suspense, type ReactNode } from 'react'
import { createBrowserRouter, RouterProvider, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom'

import { useAuth } from '@/hooks/useAuth'
import { useOAuth } from '@/hooks/useOAuth'
import { getNeedsAccount } from '@/hooks/useSetup'
import { ModulesContext, type Module } from '@/hooks/useModule'
import { Loading, LoginPrompt, AuthError, ForgotPassword, ResetPassword, Dashboard, Search, Sources, Calendar, Tasks, NotesPage, Jobs, CeleryOverview, ReportsPage } from '@/components'
import { AgentsPage, AgentEditor, AgentRuns, RunView, AdHocRun, ServersPage, ServerDetail, KeysPage, SubscriptionsPage } from '@/components/agents'
import WebhookLog from '@/components/sources/WebhookLog'
import { PollList, PollCreate, PollEdit, PollRespond, PollResults } from '@/components/polls'
import { UserSettings, UserManagement } from '@/components/users'
import { SettingsPage } from '@/components/settings'
import { PeopleManagement } from '@/components/people'
import { SetupWizard } from '@/components/setup/SetupWizard'
import { SSHKeysPage } from '@/components/SSHKeysPage'
import IngestionOverview from '@/components/IngestionOverview'
import Projects from '@/components/projects/Projects'
import Teams from '@/components/teams/Teams'
import { AppShell } from '@/components/shared/AppShell'
import { ModuleOff } from '@/components/shared/ModuleOff'
import { isVisible } from '@/components/shared/nav'
import { LoadingState } from '@/components/shared/ui'

// Lazy load heavy components (recharts ~300KB)
const Metrics = lazy(() => import('@/components/metrics/Metrics'))
const Usage = lazy(() => import('@/components/usage/Usage'))

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
            navigate('/dashboard', { replace: true })
          })
        }
      })
    }
  }, [handleCallback, checkAuth, navigate])

  // Handle redirects based on auth state changes
  useEffect(() => {
    if (!isLoading) {
      if (location.pathname === '/logout') {
        logout()
        navigate('/login', { replace: true })
      } else if (isAuthenticated) {
        // If authenticated and on login page, redirect to dashboard
        if (location.pathname === '/login' || location.pathname === '/') {
          navigate('/dashboard', { replace: true })
        }
      } else {
        // If not authenticated and on protected route, redirect to login
        // Allow public poll routes without auth
        const isPublicRoute =
          location.pathname === '/login' ||
          location.pathname === '/setup' ||
          location.pathname === '/forgot-password' ||
          location.pathname === '/reset-password' ||
          location.pathname.startsWith('/polls/respond/') ||
          location.pathname.startsWith('/polls/results/')
        if (!isPublicRoute) {
          navigate('/login', { replace: true })
        }
      }
    }
  }, [isAuthenticated, isLoading, location.pathname, navigate])

  // A server with no account yet has nobody to log in as, so its first visitor goes to setup.
  useEffect(() => {
    if (isLoading || isAuthenticated || location.pathname !== '/login') return
    getNeedsAccount()
      .then(needed => needed && navigate('/setup', { replace: true }))
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
    { path: '/dashboard', element: <Dashboard hasScope={hasScope} /> },
    { path: '/settings', element: <UserSettings /> },
    { path: '/users', element: <UserManagement />, scope: 'admin:users' },
    { path: '/search', element: <Search /> },
    { path: '/sources', element: <Sources /> },
    { path: '/sources/webhook-log', element: <WebhookLog /> },
    { path: '/ingestion', element: <IngestionOverview standalone /> },
    { path: '/ssh-keys', element: <SSHKeysPage /> },
    { path: '/calendar', element: <Calendar /> },
    { path: '/tasks', element: <Tasks /> },
    { path: '/metrics', element: <Suspense fallback={<LoadingState />}><Metrics /></Suspense> },
    { path: '/notes', element: <NotesPage /> },
    { path: '/reports', element: <ReportsPage /> },
    { path: '/people', element: <PeopleManagement />, scope: 'people' },
    { path: '/projects', element: <Projects />, module: 'teams' },
    { path: '/teams', element: <Teams />, module: 'teams' },
    { path: '/usage', element: <Suspense fallback={<LoadingState />}><Usage /></Suspense> },
    { path: '/jobs', element: <Jobs /> },
    { path: '/agents', element: <AgentsPage />, scope: 'agents' },
    { path: '/keys', element: <KeysPage /> },
    { path: '/agents/subscriptions', element: <SubscriptionsPage />, scope: 'agents' },
    { path: '/agents/servers', element: <ServersPage />, scope: 'agents' },
    { path: '/agents/servers/:id', element: <ServerDetail />, scope: 'agents' },
    { path: '/agents/runs', element: <AgentRuns />, scope: 'agents' },
    { path: '/agents/runs/new', element: <AdHocRun />, scope: 'agents' },
    { path: '/agents/runs/:runId', element: <RunView />, scope: 'agents' },
    { path: '/agents/:id', element: <AgentEditor />, scope: 'agents' },
    { path: '/server-settings', element: <SettingsPage onRestarted={checkAuth} />, scope: 'admin' },
    { path: '/celery', element: <CeleryOverview />, scope: 'admin' },
    { path: '/polls', element: <PollList /> },
    { path: '/polls/new', element: <PollCreate /> },
    { path: '/polls/edit/:slug', element: <PollEdit /> },
  ]
  const home = <Navigate to={isAuthenticated ? '/dashboard' : '/login'} replace />
  // Results are public, but a signed-in owner reaches them from Polls and should keep the app chrome.
  const pollResults = <Route path="/polls/results/:slug" element={<PollResults />} />

  return (
    <ModulesContext.Provider value={modules}>
      <Routes>
        <Route path="/login" element={isAuthenticated ? home : <LoginPrompt onLogin={startOAuth} />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/reset-password" element={<ResetPassword />} />
        <Route path="/setup" element={<SetupWizard isAuthenticated={isAuthenticated} user={user} checkAuth={checkAuth} />} />

        <Route element={<AppShell user={user} onLogout={logout} hasScope={hasScope} />}>
          {routes.map(({ path, element, scope, module }) => {
            const allowed = isAuthenticated && isVisible({ scope }, hasScope, modules)
            const page = module && !modules.has(module) ? <ModuleOff path={path} module={module} isAdmin={hasScope('admin')} /> : element
            return <Route key={path} path={path} element={allowed ? page : home} />
          })}
          {isAuthenticated && pollResults}
        </Route>

        {/* Poll respond/results are public (no auth required) */}
        <Route path="/polls/respond/:slug" element={<PollRespond />} />
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

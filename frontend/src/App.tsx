import { useEffect } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, useNavigate, useLocation } from 'react-router-dom'

import { useAuth } from '@/hooks/useAuth'
import { useOAuth } from '@/hooks/useOAuth'
import { Loading, LoginPrompt, AuthError, Dashboard, Search, Sources, Calendar, Tasks, Metrics, NotesPage, Telemetry, Jobs, DockerLogs, Snapshots, ClaudeSessions } from '@/components'
import { PollList, PollCreate, PollEdit, PollRespond, PollResults } from '@/components/polls'
import { UserSettings, UserManagement } from '@/components/users'

// AuthWrapper handles redirects based on auth state
const AuthWrapper = () => {
  const { isAuthenticated, isLoading, logout, checkAuth, user, hasScope } = useAuth()
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
          location.pathname.startsWith('/ui/polls/respond/') ||
          location.pathname.startsWith('/ui/polls/results/')
        if (!isPublicRoute) {
          navigate('/ui/login', { replace: true })
        }
      }
    }
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

  return (
    <Routes>
      {/* Public routes */}
      <Route path="/ui/login" element={
        !isAuthenticated ? (
          <LoginPrompt onLogin={startOAuth} />
        ) : (
          <Navigate to="/ui/dashboard" replace />
        )
      } />

      {/* Protected routes */}
      <Route path="/ui/dashboard" element={
        isAuthenticated ? (
          <Dashboard onLogout={logout} user={user} hasScope={hasScope} />
        ) : (
          <Navigate to="/ui/login" replace />
        )
      } />

      <Route path="/ui/settings" element={
        isAuthenticated ? (
          <UserSettings />
        ) : (
          <Navigate to="/ui/login" replace />
        )
      } />

      <Route path="/ui/users" element={
        isAuthenticated && hasScope('admin:users') ? (
          <UserManagement />
        ) : isAuthenticated ? (
          <Navigate to="/ui/dashboard" replace />
        ) : (
          <Navigate to="/ui/login" replace />
        )
      } />

      <Route path="/ui/search" element={
        isAuthenticated ? (
          <Search />
        ) : (
          <Navigate to="/ui/login" replace />
        )
      } />

      <Route path="/ui/sources" element={
        isAuthenticated ? (
          <Sources />
        ) : (
          <Navigate to="/ui/login" replace />
        )
      } />

      <Route path="/ui/calendar" element={
        isAuthenticated ? (
          <Calendar />
        ) : (
          <Navigate to="/ui/login" replace />
        )
      } />

      <Route path="/ui/tasks" element={
        isAuthenticated ? (
          <Tasks />
        ) : (
          <Navigate to="/ui/login" replace />
        )
      } />

      <Route path="/ui/metrics" element={
        isAuthenticated ? (
          <Metrics />
        ) : (
          <Navigate to="/ui/login" replace />
        )
      } />

      <Route path="/ui/notes" element={
        isAuthenticated ? (
          <NotesPage />
        ) : (
          <Navigate to="/ui/login" replace />
        )
      } />

      <Route path="/ui/telemetry" element={
        isAuthenticated ? (
          <Telemetry />
        ) : (
          <Navigate to="/ui/login" replace />
        )
      } />

      <Route path="/ui/jobs" element={
        isAuthenticated ? (
          <Jobs />
        ) : (
          <Navigate to="/ui/login" replace />
        )
      } />

      <Route path="/ui/logs" element={
        isAuthenticated ? (
          <DockerLogs />
        ) : (
          <Navigate to="/ui/login" replace />
        )
      } />

      <Route path="/ui/snapshots" element={
        isAuthenticated ? (
          <Snapshots />
        ) : (
          <Navigate to="/ui/login" replace />
        )
      } />

      <Route path="/ui/claude" element={
        isAuthenticated ? (
          <ClaudeSessions />
        ) : (
          <Navigate to="/ui/login" replace />
        )
      } />

      {/* Poll routes - authenticated */}
      <Route path="/ui/polls" element={
        isAuthenticated ? (
          <PollList />
        ) : (
          <Navigate to="/ui/login" replace />
        )
      } />
      <Route path="/ui/polls/new" element={
        isAuthenticated ? (
          <PollCreate />
        ) : (
          <Navigate to="/ui/login" replace />
        )
      } />
      <Route path="/ui/polls/edit/:slug" element={
        isAuthenticated ? (
          <PollEdit />
        ) : (
          <Navigate to="/ui/login" replace />
        )
      } />

      {/* Poll routes - public (no auth required) */}
      <Route path="/ui/polls/respond/:slug" element={<PollRespond />} />
      <Route path="/ui/polls/results/:slug" element={<PollResults />} />

      {/* Default redirect */}
      <Route path="/" element={
        <Navigate to={isAuthenticated ? "/ui/dashboard" : "/ui/login"} replace />
      } />

      {/* Catch-all redirect */}
      <Route path="*" element={
        <Navigate to={isAuthenticated ? "/ui/dashboard" : "/ui/login"} replace />
      } />
    </Routes>
  )
}

function App() {
  return (
    <Router>
      <div className="min-h-screen flex flex-col">
        <AuthWrapper />
      </div>
    </Router>
  )
}

export default App

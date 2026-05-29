import { describe, it, expect, vi } from 'vitest'
import { renderWithRouter, screen } from '@/test/utils'
import type { AuthUser } from '../hooks/useAuth'
import Dashboard from './Dashboard'

const makeUser = (overrides: Partial<AuthUser> = {}): AuthUser => ({
  id: 1,
  name: 'Ada Lovelace',
  email: 'ada@example.com',
  user_type: 'human',
  scopes: ['*'],
  ...overrides,
})

const renderDashboard = (props: Partial<React.ComponentProps<typeof Dashboard>> = {}) =>
  renderWithRouter(
    <Dashboard
      onLogout={props.onLogout ?? (() => {})}
      user={props.user === undefined ? makeUser() : props.user}
      hasScope={props.hasScope ?? (() => false)}
    />,
  )

describe('Dashboard', () => {
  it('renders the welcome banner and section headings', () => {
    renderDashboard()
    expect(screen.getByText('Welcome to your Memory Database!')).toBeInTheDocument()
    expect(screen.getByText('Knowledge & Content')).toBeInTheDocument()
    expect(screen.getByText('Productivity')).toBeInTheDocument()
    expect(screen.getByText('AI')).toBeInTheDocument()
    expect(screen.getByText('System Operations')).toBeInTheDocument()
  })

  it('renders the user menu when a user is present', () => {
    renderDashboard({ user: makeUser() })
    expect(screen.getByRole('button', { name: 'User menu' })).toBeInTheDocument()
  })

  it('omits the user menu when user is null', () => {
    renderDashboard({ user: null })
    expect(screen.queryByRole('button', { name: 'User menu' })).not.toBeInTheDocument()
  })

  it.each([
    ['Search', '/ui/search'],
    ['Sources', '/ui/sources'],
    ['Notes', '/ui/notes'],
    ['Reports', '/ui/reports'],
    ['Calendar', '/ui/calendar'],
    ['Tasks', '/ui/tasks'],
    ['Claude Sessions', '/ui/claude'],
    ['Docker Logs', '/ui/logs'],
    ['Scheduled Tasks', '/ui/scheduled-tasks'],
  ])('links the %s card to %s', (label, href) => {
    renderDashboard()
    const heading = screen.getByText(label)
    const link = heading.closest('a')
    expect(link).toHaveAttribute('href', href)
  })

  it('hides admin-only cards when the user lacks scopes', () => {
    renderDashboard({ hasScope: () => false })
    expect(screen.queryByText('Celery Tasks')).not.toBeInTheDocument()
    expect(screen.queryByText('User Management')).not.toBeInTheDocument()
  })

  it('shows the Celery card only with the admin scope', () => {
    renderDashboard({ hasScope: (s) => s === 'admin' })
    expect(screen.getByText('Celery Tasks')).toBeInTheDocument()
    expect(screen.queryByText('User Management')).not.toBeInTheDocument()
  })

  it('shows the User Management card only with the admin:users scope', () => {
    renderDashboard({ hasScope: (s) => s === 'admin:users' })
    expect(screen.getByText('User Management')).toBeInTheDocument()
    expect(screen.queryByText('Celery Tasks')).not.toBeInTheDocument()
  })

  it('forwards logout to the user menu', async () => {
    const onLogout = vi.fn()
    const { user } = renderDashboard({ onLogout, user: makeUser() })
    await user.click(screen.getByRole('button', { name: 'User menu' }))
    await user.click(screen.getByRole('menuitem', { name: /Logout/ }))
    expect(onLogout).toHaveBeenCalledTimes(1)
  })
})

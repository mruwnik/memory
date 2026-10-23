import { describe, it, expect } from 'vitest'
import { renderWithRouter, screen } from '@/test/utils'
import Dashboard from './Dashboard'

const renderDashboard = (hasScope: (scope: string) => boolean = () => false) =>
  renderWithRouter(<Dashboard hasScope={hasScope} />)

describe('Dashboard', () => {
  it('renders the section headings', () => {
    renderDashboard()
    expect(screen.getByText('Knowledge & Content')).toBeInTheDocument()
    expect(screen.getByText('Productivity')).toBeInTheDocument()
    expect(screen.getByText('AI')).toBeInTheDocument()
    expect(screen.getByText('System Operations')).toBeInTheDocument()
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
    renderDashboard(() => false)
    expect(screen.queryByText('Celery Tasks')).not.toBeInTheDocument()
    expect(screen.queryByText('User Management')).not.toBeInTheDocument()
  })

  it('shows the Celery card only with the admin scope', () => {
    renderDashboard(s => s === 'admin')
    expect(screen.getByText('Celery Tasks')).toBeInTheDocument()
    expect(screen.queryByText('User Management')).not.toBeInTheDocument()
  })

  it('shows the User Management card only with the admin:users scope', () => {
    renderDashboard(s => s === 'admin:users')
    expect(screen.getByText('User Management')).toBeInTheDocument()
    expect(screen.queryByText('Celery Tasks')).not.toBeInTheDocument()
  })
})

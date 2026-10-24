import { describe, it, expect } from 'vitest'
import { renderWithRouter, screen } from '@/test/utils'
import Dashboard from './Dashboard'

const renderDashboard = (hasScope: (scope: string) => boolean = () => false) =>
  renderWithRouter(<Dashboard hasScope={hasScope} />)

describe('Dashboard', () => {
  it('renders the section headings', () => {
    renderDashboard()
    expect(screen.getByText('Knowledge & content')).toBeInTheDocument()
    expect(screen.getByText('Productivity')).toBeInTheDocument()
    expect(screen.getByText('AI')).toBeInTheDocument()
    expect(screen.getByText('System operations')).toBeInTheDocument()
  })

  it.each([
    ['Search', '/ui/search'],
    ['Sources', '/ui/sources'],
    ['Notes', '/ui/notes'],
    ['Reports', '/ui/reports'],
    ['Calendar', '/ui/calendar'],
    ['Tasks', '/ui/tasks'],
    ['Scheduled tasks', '/ui/scheduled-tasks'],
  ])('links the %s card to %s', (label, href) => {
    renderDashboard()
    const heading = screen.getByText(label)
    const link = heading.closest('a')
    expect(link).toHaveAttribute('href', href)
  })

  it('hides admin-only cards when the user lacks scopes', () => {
    renderDashboard(() => false)
    expect(screen.queryByText('Celery tasks')).not.toBeInTheDocument()
    expect(screen.queryByText('User management')).not.toBeInTheDocument()
    expect(screen.queryByText('Claude sessions')).not.toBeInTheDocument()
  })

  it('shows the Celery card only with the admin scope', () => {
    renderDashboard(s => s === 'admin')
    expect(screen.getByText('Celery tasks')).toBeInTheDocument()
    expect(screen.queryByText('User management')).not.toBeInTheDocument()
  })

  it('shows the Claude Sessions card only with the claude_sessions scope', () => {
    renderDashboard(s => s === 'claude_sessions')
    expect(screen.getByText('Claude sessions').closest('a')).toHaveAttribute('href', '/ui/claude')
  })

  it('shows the User Management card only with the admin:users scope', () => {
    renderDashboard(s => s === 'admin:users')
    expect(screen.getByText('User management')).toBeInTheDocument()
    expect(screen.queryByText('Celery tasks')).not.toBeInTheDocument()
  })
})

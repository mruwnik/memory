import { describe, it, expect, vi } from 'vitest'
import { renderWithRouter, screen } from '@/test/utils'
import Dashboard from './Dashboard'
vi.mock('./IngestionOverview', () => ({ default: () => <div>Ingestion overview</div> }))

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
    ['Search', '/search'],
    ['Sources', '/sources'],
    ['Notes', '/notes'],
    ['Reports', '/reports'],
    ['Calendar', '/calendar'],
    ['Tasks', '/tasks'],
  ])('links the %s card to %s', (label, href) => {
    renderDashboard(() => true)
    const heading = screen.getByText(label)
    const link = heading.closest('a')
    expect(link).toHaveAttribute('href', href)
  })

  it('hides admin-only cards when the user lacks scopes', () => {
    renderDashboard(() => false)
    expect(screen.queryByText('Celery tasks')).not.toBeInTheDocument()
    expect(screen.queryByText('User management')).not.toBeInTheDocument()
  })

  it('shows the Celery card only with the admin scope', () => {
    renderDashboard(s => s === 'admin')
    expect(screen.getByText('Celery tasks')).toBeInTheDocument()
    expect(screen.queryByText('User management')).not.toBeInTheDocument()
  })

  it('shows the User Management card only with the admin:users scope', () => {
    renderDashboard(s => s === 'admin:users')
    expect(screen.getByText('User management')).toBeInTheDocument()
    expect(screen.queryByText('Celery tasks')).not.toBeInTheDocument()
  })
})

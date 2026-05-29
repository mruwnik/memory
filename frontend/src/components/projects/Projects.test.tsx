import { describe, it, expect, vi } from 'vitest'
import { renderWithRouter, screen } from '@/test/utils'

// The page is a thin wrapper around ProjectsPanel (owned/tested elsewhere); mock
// it so this test isolates the page chrome (heading + back link).
vi.mock('../sources/panels/ProjectsPanel', () => ({
  ProjectsPanel: () => <div data-testid="projects-panel">panel</div>,
}))

import Projects from './Projects'

describe('Projects page', () => {
  it('renders the heading, a back link to the dashboard, and the panel', () => {
    renderWithRouter(<Projects />)
    expect(screen.getByRole('heading', { name: 'Projects' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back' })).toHaveAttribute('href', '/ui/dashboard')
    expect(screen.getByTestId('projects-panel')).toBeInTheDocument()
  })
})

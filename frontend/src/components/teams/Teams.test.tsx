import { describe, it, expect, vi } from 'vitest'
import { renderWithRouter, screen } from '@/test/utils'

// The page is a thin wrapper around TeamsPanel (owned/tested elsewhere); mock it
// so this test isolates the page chrome (heading + back link).
vi.mock('../sources/panels/TeamsPanel', () => ({
  TeamsPanel: () => <div data-testid="teams-panel">panel</div>,
}))

import Teams from './Teams'

describe('Teams page', () => {
  it('renders the heading, a back link to the dashboard, and the panel', () => {
    renderWithRouter(<Teams />)
    expect(screen.getByRole('heading', { name: 'Teams' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back' })).toHaveAttribute('href', '/ui/dashboard')
    expect(screen.getByTestId('teams-panel')).toBeInTheDocument()
  })
})

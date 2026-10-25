import { describe, it, expect } from 'vitest'
import { renderWithRouter, screen } from '@/test/utils'
import { ModuleOff } from './ModuleOff'

describe('ModuleOff', () => {
  it.each([
    ['/ui/teams', 'Teams'],
    ['/ui/claude/:sessionId', 'Claude sessions'],
    ['/ui/not-in-nav', 'Unavailable'],
  ])('titles %s after its section: %s', (path, title) => {
    renderWithRouter(<ModuleOff path={path} module="teams" isAdmin={false} />)
    expect(screen.getByRole('heading', { level: 1, name: title })).toBeInTheDocument()
  })
})

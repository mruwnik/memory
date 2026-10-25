import { describe, it, expect, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor, within, mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import ModulesPage from './ModulesPage'

const moduleList = (enabled: string[]) =>
  ['claude_containers', 'multi_user', 'teams'].map(name => ({
    name,
    description: `${name} description`,
    enabled: enabled.includes(name),
  }))

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('ModulesPage', () => {
  it.each([
    ['claude_containers', 'on'],
    ['teams', 'on'],
    ['multi_user', 'off'],
  ])('shows %s as %s with its description', async (module, state) => {
    mockFetchRoutes({ '/modules': { json: moduleList(['teams', 'claude_containers']) } })
    renderWithRouter(<ModulesPage />)
    const row = within(await screen.findByRole('list')).getByText(module).closest('li') as HTMLElement
    expect(within(row).getByText(state)).toBeInTheDocument()
    expect(within(row).getByText(`${module} description`)).toBeInTheDocument()
  })

  it.each([
    [['teams', 'claude_containers'], 'ENABLE_MODULES=claude_containers,teams'],
    [[], 'ENABLE_MODULES='],
  ])('shows the env line for %j', async (enabled, line) => {
    mockFetchRoutes({ '/modules': { json: moduleList(enabled) } })
    renderWithRouter(<ModulesPage />)
    expect(await screen.findByText(line)).toBeInTheDocument()
  })

  it('shows the server error', async () => {
    mockFetchRoutes({ '/modules': { status: 403, json: { detail: 'Insufficient permissions' } } })
    renderWithRouter(<ModulesPage />)
    await waitFor(() => expect(screen.getByText('Insufficient permissions')).toBeInTheDocument())
  })
})

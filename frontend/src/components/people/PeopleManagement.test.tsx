import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderWithRouter, screen, waitFor, within } from '@/test/utils'
import { mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import { mcpResult, mcpCallsTo } from '../../hooks/mcpEnvelope.testhelper'
import PeopleManagement from './PeopleManagement'

const authMe = {
  json: { user_id: 1, name: 'Admin', email: 'a@x.com', user_type: 'human', scopes: ['*'] },
}

const person = (overrides = {}) => ({
  id: 1,
  identifier: 'alice',
  display_name: 'Alice',
  aliases: [],
  contact_info: {},
  tags: [],
  created_at: null,
  ...overrides,
})

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('PeopleManagement list states', () => {
  it('shows loading state initially', () => {
    mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult([]),
      teams_list_all: mcpResult({ teams: [], count: 0 }),
    })
    renderWithRouter(<PeopleManagement />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('renders empty state when no people exist', async () => {
    mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult([]),
      teams_list_all: mcpResult({ teams: [], count: 0 }),
    })
    renderWithRouter(<PeopleManagement />)
    expect(
      await screen.findByText(/No people tracked yet/),
    ).toBeInTheDocument()
  })

  it('renders a populated list and the summary count', async () => {
    mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult([person(), person({ id: 2, identifier: 'bob', display_name: 'Bob' })]),
      teams_list_all: mcpResult({ teams: [], count: 0 }),
    })
    renderWithRouter(<PeopleManagement />)
    expect(await screen.findByRole('heading', { name: 'Alice' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Bob' })).toBeInTheDocument()
    expect(screen.getByText('Showing 2 people')).toBeInTheDocument()
  })

  it('shows an error banner when the list call rejects', async () => {
    mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: { status: 500, json: { detail: 'oops' } },
      teams_list_all: mcpResult({ teams: [], count: 0 }),
    })
    renderWithRouter(<PeopleManagement />)
    expect(await screen.findByText(/Failed to load people|failed/i)).toBeInTheDocument()
  })
})

describe('PeopleManagement create flow', () => {
  it('opens the create modal, submits, and closes on success', async () => {
    const fetchMock = mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult([]),
      teams_list_all: mcpResult({ teams: [], count: 0 }),
      people_upsert: mcpResult({ success: true }),
    })
    const { user } = renderWithRouter(<PeopleManagement />)
    await screen.findByText(/No people tracked yet/)

    await user.click(screen.getByRole('button', { name: /Add Person/ }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText(/Display Name/), 'Carol')
    await user.click(within(dialog).getByRole('button', { name: 'Add Person' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const upsert = mcpCallsTo(fetchMock, 'people_upsert')[0]
    expect(upsert).toBeTruthy()
  })

  it('shows an error inside the modal when create fails', async () => {
    mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult([]),
      teams_list_all: mcpResult({ teams: [], count: 0 }),
      people_upsert: { status: 500, json: { detail: 'nope' } },
    })
    const { user } = renderWithRouter(<PeopleManagement />)
    await screen.findByText(/No people tracked yet/)

    await user.click(screen.getByRole('button', { name: /Add Person/ }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText(/Display Name/), 'Carol')
    await user.click(within(dialog).getByRole('button', { name: 'Add Person' }))

    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toBeInTheDocument()
  })
})

describe('PeopleManagement edit flow', () => {
  it('opens the edit modal prefilled and submits an update', async () => {
    const fetchMock = mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult([person()]),
      teams_list_all: mcpResult({ teams: [], count: 0 }),
      people_upsert: mcpResult({ success: true }),
    })
    const { user } = renderWithRouter(<PeopleManagement />)
    await screen.findByRole('heading', { name: 'Alice' })

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByLabelText(/Display Name/)).toHaveValue('Alice')
    await user.click(within(dialog).getByRole('button', { name: 'Save Changes' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const upsert = mcpCallsTo(fetchMock, 'people_upsert')[0]
    expect(upsert).toBeTruthy()
  })
})

describe('PeopleManagement delete flow', () => {
  it('confirms deletion via the modal and issues the delete call', async () => {
    const fetchMock = mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult([person()]),
      teams_list_all: mcpResult({ teams: [], count: 0 }),
      people_delete: mcpResult({ deleted: true, identifier: 'alice', display_name: 'Alice' }),
    })
    const { user } = renderWithRouter(<PeopleManagement />)
    await screen.findByRole('heading', { name: 'Alice' })

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(screen.getByRole('heading', { name: 'Delete Person' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Delete Person' }))

    await waitFor(() => {
      const del = mcpCallsTo(fetchMock, 'people_delete')[0]
      expect(del).toBeTruthy()
    })
  })

  it('cancels deletion without calling delete', async () => {
    const fetchMock = mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult([person()]),
      teams_list_all: mcpResult({ teams: [], count: 0 }),
    })
    const { user } = renderWithRouter(<PeopleManagement />)
    await screen.findByRole('heading', { name: 'Alice' })

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(screen.getByRole('heading', { name: 'Delete Person' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Delete Person' })).not.toBeInTheDocument(),
    )
    const del = mcpCallsTo(fetchMock, 'people_delete')[0]
    expect(del).toBeFalsy()
  })
})

describe('PeopleManagement search and tag filtering', () => {
  it('debounces search input and forwards the term to people_list_all', async () => {
    const fetchMock = mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult([person()]),
      teams_list_all: mcpResult({ teams: [], count: 0 }),
    })
    const { user } = renderWithRouter(<PeopleManagement />)
    await screen.findByRole('heading', { name: 'Alice' })

    await user.type(screen.getByPlaceholderText(/Search by name/), 'bob')

    await waitFor(() => {
      const searchCall = mcpCallsTo(fetchMock, 'people_list_all').find(
        (c) => JSON.parse(c[1].body).params.arguments.search === 'bob',
      )
      expect(searchCall).toBeTruthy()
    })
  })

  it('toggles a tag filter and re-queries with the selected tag', async () => {
    const fetchMock = mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult([person({ tags: ['vip'] })]),
      teams_list_all: mcpResult({ teams: [], count: 0 }),
    })
    const { user } = renderWithRouter(<PeopleManagement />)
    await screen.findByRole('heading', { name: 'Alice' })

    await user.click(screen.getByText('Filter by tag'))
    await user.click(screen.getByRole('button', { name: 'vip' }))

    await waitFor(() => {
      const tagCall = mcpCallsTo(fetchMock, 'people_list_all').find((c) => {
        const { tags } = JSON.parse(c[1].body).params.arguments
        return Array.isArray(tags) && tags.includes('vip')
      })
      expect(tagCall).toBeTruthy()
    })
  })

  it('shows the "no match" empty message when searching yields nothing', async () => {
    mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult([]),
      teams_list_all: mcpResult({ teams: [], count: 0 }),
    })
    const { user } = renderWithRouter(<PeopleManagement />)
    await screen.findByText(/No people tracked yet/)
    await user.type(screen.getByPlaceholderText(/Search by name/), 'zzz')
    expect(await screen.findByText(/No people found matching your search/)).toBeInTheDocument()
  })
})

describe('PeopleManagement team filter', () => {
  it('renders the team filter dropdown when teams exist and filters by team', async () => {
    const fetchMock = mockFetchRoutes({
      '/auth/me': authMe,
      teams_list_all: mcpResult({ teams: [{ id: 7, name: 'Eng', slug: 'eng', member_count: 3 }], count: 1 }),
      people_list_all: mcpResult([person(), person({ id: 2, identifier: 'bob', display_name: 'Bob' })]),
      teams_fetch: mcpResult({ team: { id: 7, members: [{ id: 1, identifier: 'alice', display_name: 'Alice' }] } }),
    })
    const { user } = renderWithRouter(<PeopleManagement />)
    await screen.findByRole('heading', { name: 'Alice' })

    await user.click(screen.getByRole('button', { name: /All Teams/ }))
    await user.click(screen.getByRole('button', { name: /Eng/ }))

    // Only Alice (a member of team 7) remains after the team filter
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Bob' })).not.toBeInTheDocument())
    expect(screen.getByRole('heading', { name: 'Alice' })).toBeInTheDocument()
    expect(mcpCallsTo(fetchMock, 'teams_fetch').length > 0).toBe(true)
  })
})

describe('PeopleManagement merge flow', () => {
  it('selects two people, opens the merge modal, and merges', async () => {
    const fetchMock = mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult([
        person(),
        person({ id: 2, identifier: 'bob', display_name: 'Bob' }),
      ]),
      teams_list_all: mcpResult({ teams: [], count: 0 }),
      people_merge: mcpResult({ success: true, merged_from: ['bob'] }),
    })
    const { user } = renderWithRouter(<PeopleManagement />)
    await screen.findByRole('heading', { name: 'Alice' })

    const checkboxes = screen.getAllByTitle('Select for merge')
    await user.click(checkboxes[0])
    await user.click(checkboxes[1])
    expect(screen.getByText('2 selected')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Merge$/ }))
    expect(screen.getByRole('heading', { name: 'Merge People' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Merge People' }))

    await waitFor(() => {
      const merge = mcpCallsTo(fetchMock, 'people_merge')[0]
      expect(merge).toBeTruthy()
    })
  })

  it('clears merge selection', async () => {
    mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult([person(), person({ id: 2, identifier: 'bob', display_name: 'Bob' })]),
      teams_list_all: mcpResult({ teams: [], count: 0 }),
    })
    const { user } = renderWithRouter(<PeopleManagement />)
    await screen.findByRole('heading', { name: 'Alice' })

    const checkboxes = screen.getAllByTitle('Select for merge')
    await user.click(checkboxes[0])
    expect(screen.getByText('1 selected')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clear' }))
    expect(screen.queryByText('1 selected')).not.toBeInTheDocument()
  })
})

describe('PeopleManagement expand', () => {
  it('fetches teams and tidbits when a card is expanded', async () => {
    const fetchMock = mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult([person({ aliases: ['ali'] })]),
      teams_list_all: mcpResult({ teams: [], count: 0 }),
      people_fetch: mcpResult({ person: { teams: [{ id: 3, name: 'Eng' }] } }),
    })
    const { user } = renderWithRouter(<PeopleManagement />)
    await screen.findByRole('heading', { name: 'Alice' })

    await user.click(screen.getByText('@alice'))

    await waitFor(() => {
      expect(mcpCallsTo(fetchMock, 'people_fetch').length > 0).toBe(true)
    })
  })
})

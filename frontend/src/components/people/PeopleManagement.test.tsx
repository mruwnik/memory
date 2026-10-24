import { describe, it, expect, beforeEach, vi } from 'vitest'
import userEvent from '@testing-library/user-event'
import { renderWithRouter, screen, waitFor, within } from '@/test/utils'
import { mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import { mcpResult, mcpToolError, mcpCallsTo } from '../../hooks/mcpEnvelope.testhelper'
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
    expect(await screen.findByText('Alice')).toBeInTheDocument()
    expect(screen.getByText('Bob')).toBeInTheDocument()
    expect(screen.getByText('Showing 2 people')).toBeInTheDocument()
  })

  it('says the list was capped even when a team filter leaves fewer people', async () => {
    const many = Array.from({ length: 200 }, (_, i) => person({ id: i + 1, identifier: `p${i}`, display_name: `P${i}` }))
    mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult(many),
      teams_list_all: mcpResult({ teams: [{ id: 5, name: 'Core' }], count: 1 }),
      teams_fetch: mcpResult({ team: { id: 5, name: 'Core', members: [{ identifier: 'p1' }] } }),
    })
    const user = userEvent.setup()
    renderWithRouter(<PeopleManagement />)
    await user.selectOptions(await screen.findByRole('combobox', { name: 'Team' }), '5')
    expect(await screen.findByText(/Showing 1 person, from the first 200 loaded/)).toBeInTheDocument()
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

    await user.click(screen.getByRole('button', { name: 'New person' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText(/Display name/), 'Carol')
    await user.click(within(dialog).getByRole('button', { name: 'Create person' }))

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

    await user.click(screen.getByRole('button', { name: 'New person' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText(/Display name/), 'Carol')
    await user.click(within(dialog).getByRole('button', { name: 'Create person' }))

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
    await screen.findByText('Alice')

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByLabelText(/Display name/)).toHaveValue('Alice')
    await user.click(within(dialog).getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    const upsert = mcpCallsTo(fetchMock, 'people_upsert')[0]
    expect(upsert).toBeTruthy()
  })
})

describe('PeopleManagement delete flow', () => {
  it('confirms deletion via the dialog and issues the delete call', async () => {
    const fetchMock = mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult([person()]),
      teams_list_all: mcpResult({ teams: [], count: 0 }),
      people_delete: mcpResult({ deleted: true, identifier: 'alice', display_name: 'Alice' }),
    })
    const { user } = renderWithRouter(<PeopleManagement />)
    await screen.findByText('Alice')

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))

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
    await screen.findByText('Alice')

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
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
    await screen.findByText('Alice')

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
    await screen.findByText('Alice')

    await user.click(screen.getByRole('combobox', { name: 'Filter by tag' }))
    await user.click(screen.getByRole('option', { name: 'vip' }))

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
    await screen.findByText('Alice')

    await user.selectOptions(screen.getByRole('combobox', { name: 'Team' }), '7')

    // Only Alice (a member of team 7) remains after the team filter
    await waitFor(() => expect(screen.queryByText('Bob')).not.toBeInTheDocument())
    expect(screen.getByText('Alice')).toBeInTheDocument()
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
    await screen.findByText('Alice')

    const checkboxes = screen.getAllByRole('checkbox', { name: /for merge/ })
    await user.click(checkboxes[0])
    await user.click(checkboxes[1])
    expect(screen.getByText('2 selected')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Merge$/ }))
    expect(screen.getByRole('heading', { name: 'Merge people' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Merge people' }))

    await waitFor(() => {
      const merge = mcpCallsTo(fetchMock, 'people_merge')[0]
      expect(merge).toBeTruthy()
    })
  })

  it('shows a merge failure inside the merge modal', async () => {
    mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult([person(), person({ id: 2, identifier: 'bob', display_name: 'Bob' })]),
      teams_list_all: mcpResult({ teams: [], count: 0 }),
      people_merge: mcpResult({ success: false, error: 'merge refused' }),
    })
    const { user } = renderWithRouter(<PeopleManagement />)
    await screen.findByText('Alice')
    const checkboxes = screen.getAllByRole('checkbox', { name: /for merge/ })
    await user.click(checkboxes[0])
    expect(screen.getByText('Select one more person to merge')).toBeInTheDocument()
    await user.click(checkboxes[1])
    await user.click(screen.getByRole('button', { name: /Merge$/ }))
    await user.click(screen.getByRole('button', { name: 'Merge people' }))
    expect(await within(screen.getByRole('dialog')).findByText('merge refused')).toBeInTheDocument()
  })

  it('clears merge selection', async () => {
    mockFetchRoutes({
      '/auth/me': authMe,
      people_list_all: mcpResult([person(), person({ id: 2, identifier: 'bob', display_name: 'Bob' })]),
      teams_list_all: mcpResult({ teams: [], count: 0 }),
    })
    const { user } = renderWithRouter(<PeopleManagement />)
    await screen.findByText('Alice')

    const checkboxes = screen.getAllByRole('checkbox', { name: /for merge/ })
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
    await screen.findByText('Alice')

    await user.click(screen.getByRole('button', { name: 'Alice' }))

    await waitFor(() => {
      expect(mcpCallsTo(fetchMock, 'people_fetch').length > 0).toBe(true)
    })
  })
})

describe('PeopleManagement tidbits', () => {
  const tidbit = { id: 7, person_id: 1, content: 'Likes tea', tidbit_type: 'preference', source: null, sensitivity: 'basic', project_id: null, tags: ['drinks'], creator_id: 1, created_at: null }

  const routes = (extra: Record<string, ReturnType<typeof mcpResult>>) => mockFetchRoutes({
    '/auth/me': authMe,
    people_list_all: mcpResult([person()]),
    teams_list_all: mcpResult({ teams: [], count: 0 }),
    teams_fetch: mcpResult({ teams: [] }),
    people_fetch: mcpResult(person({ tidbits: [tidbit] })),
    ...extra,
  })

  const expand = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(await screen.findByRole('button', { name: 'Alice' }))
    await screen.findByText('Likes tea')
  }

  it('adds a tidbit with the form values', async () => {
    const fetchMock = routes({ people_tidbit_add: mcpResult({ status: 'queued' }) })
    const { user } = renderWithRouter(<PeopleManagement />)
    await expand(user)

    await user.click(screen.getByRole('button', { name: 'Add tidbit' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Only visible to you.')).toBeInTheDocument()
    await user.type(within(dialog).getByLabelText(/Content/), 'Allergic to nuts')
    await user.type(within(dialog).getByLabelText('Tags'), 'health, food')
    await user.selectOptions(within(dialog).getByLabelText('Sensitivity'), 'internal')
    await user.click(within(dialog).getByRole('button', { name: 'Add tidbit' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByText('Allergic to nuts')).toBeInTheDocument()
    expect(JSON.parse(mcpCallsTo(fetchMock, 'people_tidbit_add')[0][1].body).params.arguments).toEqual({
      identifier: 'alice', content: 'Allergic to nuts', tidbit_type: 'note', tags: ['health', 'food'], sensitivity: 'internal',
    })
  })

  it('edits a tidbit, prefilled, by id', async () => {
    const fetchMock = routes({ people_tidbit_update: mcpResult({ id: 7 }) })
    const { user } = renderWithRouter(<PeopleManagement />)
    await expand(user)

    await user.click(screen.getByRole('button', { name: /Edit tidbit: Likes tea/ }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByLabelText(/Content/)).toHaveValue('Likes tea')
    await user.type(within(dialog).getByLabelText(/Content/), ' a lot')
    await user.click(within(dialog).getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(JSON.parse(mcpCallsTo(fetchMock, 'people_tidbit_update')[0][1].body).params.arguments).toEqual({
      tidbit_id: 7, content: 'Likes tea a lot', tidbit_type: 'preference', tags: ['drinks'], sensitivity: 'basic',
    })
  })

  it('shows the server error inside the modal when saving fails', async () => {
    routes({ people_tidbit_update: mcpToolError('You can only edit tidbits you created') })
    const { user } = renderWithRouter(<PeopleManagement />)
    await expand(user)

    await user.click(screen.getByRole('button', { name: /Edit tidbit/ }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Save changes' }))

    expect(await within(screen.getByRole('dialog')).findByRole('alert')).toHaveTextContent('You can only edit tidbits you created')
  })

  it('deletes a tidbit after confirming, and shows a failure on the page', async () => {
    const fetchMock = routes({ people_tidbit_delete: mcpToolError('You can only delete tidbits you created') })
    const { user } = renderWithRouter(<PeopleManagement />)
    await expand(user)

    await user.click(screen.getByRole('button', { name: /Delete tidbit/ }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))

    expect(await screen.findByText('You can only delete tidbits you created')).toBeInTheDocument()
    expect(JSON.parse(mcpCallsTo(fetchMock, 'people_tidbit_delete')[0][1].body).params.arguments).toEqual({ tidbit_id: 7 })
  })

  it('hides Edit and Delete on tidbits the user did not create', async () => {
    routes({
      '/auth/me': { json: { ...authMe.json, user_id: 2, scopes: ['people:read'] } },
    })
    const { user } = renderWithRouter(<PeopleManagement />)
    await expand(user)

    expect(screen.getByRole('button', { name: 'Add tidbit' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Edit tidbit/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Delete tidbit/ })).not.toBeInTheDocument()
  })
})

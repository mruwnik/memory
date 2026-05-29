import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import {
  renderWithRouter,
  screen,
  waitFor,
  setAuthCookies,
  clearCookies,
  mockFetch,
  mockResponse,
} from '@/test/utils'
import PollList from './PollList'
import type { Poll } from '@/hooks/usePolls'
import { mcpEnvelopeJson } from '@/hooks/mcpEnvelope.testhelper'

function makePoll(overrides: Partial<Poll> = {}): Poll {
  return {
    id: 1,
    slug: 'team-sync',
    title: 'Team Sync',
    description: 'Weekly planning',
    status: 'open',
    datetime_start: '2024-01-15T09:00:00.000Z',
    datetime_end: '2024-01-15T17:00:00.000Z',
    slot_duration_minutes: 30,
    response_count: 2,
    created_at: '2024-01-01T00:00:00.000Z',
    closes_at: null,
    finalized_at: null,
    finalized_time: null,
    ...overrides,
  }
}

// Route MCP calls by method substring. Each entry maps method-name -> payload
// (or an Error to simulate failure). Auth's /auth/me is answered generically.
function mockMcp(handlers: Record<string, unknown | Error>) {
  return mockFetch(async (input) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url.includes('/auth/me')) {
      return mockResponse({ json: { user_id: 1, scopes: ['*'] } })
    }
    for (const [method, payload] of Object.entries(handlers)) {
      if (!url.includes(method)) continue
      if (payload instanceof Error) {
        return mockResponse({ status: 500, json: { detail: payload.message } })
      }
      return mockResponse({ json: mcpEnvelopeJson(payload) })
    }
    return mockResponse({ status: 404, json: { detail: 'not found' } })
  })
}

beforeEach(() => {
  setAuthCookies()
})

describe('PollList - loading and empty states', () => {
  it('shows a loading indicator before data resolves', () => {
    mockMcp({ polling_list_polls: [] })
    renderWithRouter(<PollList />)
    expect(screen.getByText('Loading polls...')).toBeInTheDocument()
  })

  it('shows the empty state with a first-poll CTA when no polls exist', async () => {
    mockMcp({ polling_list_polls: [] })
    renderWithRouter(<PollList />)
    expect(
      await screen.findByText("You haven't created any polls yet."),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: 'Create Your First Poll' }),
    ).toBeInTheDocument()
  })
})

describe('PollList - populated list', () => {
  it('renders poll titles, response counts, and status badges', async () => {
    mockMcp({
      polling_list_polls: [
        makePoll({ id: 1, title: 'Alpha', response_count: 1 }),
        makePoll({ id: 2, slug: 'beta', title: 'Beta', status: 'closed', response_count: 3 }),
      ],
    })
    renderWithRouter(<PollList />)
    expect(await screen.findByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText('Beta')).toBeInTheDocument()
    expect(screen.getByText('1 response')).toBeInTheDocument()
    expect(screen.getByText('3 responses')).toBeInTheDocument()
    expect(screen.getByText('open')).toBeInTheDocument()
    expect(screen.getByText('closed')).toBeInTheDocument()
  })

  it('renders results/edit/share links pointing at the poll slug', async () => {
    mockMcp({ polling_list_polls: [makePoll({ slug: 'team-sync' })] })
    renderWithRouter(<PollList />)
    await screen.findByText('Team Sync')
    expect(screen.getByRole('link', { name: 'View Results' })).toHaveAttribute(
      'href',
      '/ui/polls/results/team-sync',
    )
    expect(screen.getByRole('link', { name: 'Edit' })).toHaveAttribute(
      'href',
      '/ui/polls/edit/team-sync',
    )
    expect(screen.getByRole('link', { name: 'Share' })).toHaveAttribute(
      'href',
      '/ui/polls/respond/team-sync',
    )
  })

  it('renders a finalized meeting banner when finalized_time is set', async () => {
    mockMcp({
      polling_list_polls: [
        makePoll({ status: 'finalized', finalized_time: '2024-01-20T15:00:00.000Z' }),
      ],
    })
    renderWithRouter(<PollList />)
    expect(await screen.findByText(/Meeting:/)).toBeInTheDocument()
  })

  it('omits the description block when there is no description', async () => {
    mockMcp({ polling_list_polls: [makePoll({ description: null })] })
    renderWithRouter(<PollList />)
    await screen.findByText('Team Sync')
    expect(screen.queryByText('Weekly planning')).not.toBeInTheDocument()
  })
})

describe('PollList - load error', () => {
  it('shows an error banner when listing fails', async () => {
    mockMcp({ polling_list_polls: new Error('boom') })
    renderWithRouter(<PollList />)
    expect(await screen.findByText(/MCP polling_list_polls failed|boom/)).toBeInTheDocument()
  })
})

describe('PollList - status filter', () => {
  it('refetches with the selected status and updates the empty message', async () => {
    const fetchMock = mockMcp({ polling_list_polls: [] })
    const { user } = renderWithRouter(<PollList />)
    await screen.findByText("You haven't created any polls yet.")
    await user.selectOptions(screen.getByRole('combobox'), 'closed')
    expect(await screen.findByText('No closed polls found.')).toBeInTheDocument()
    // The most recent list call carried the chosen status filter.
    const listCall = fetchMock.mock.calls.find(([u]) =>
      String(u).includes('polling_list_polls'),
    )
    expect(listCall).toBeTruthy()
  })
})

describe('PollList - delete flow', () => {
  it('removes the poll after confirming deletion', async () => {
    mockMcp({
      polling_list_polls: [makePoll({ id: 7, title: 'Doomed' })],
      polling_delete_poll: { deleted: true, poll_id: 7 },
    })
    const { user } = renderWithRouter(<PollList />)
    await screen.findByText('Doomed')
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    // Confirmation appears in place of the plain delete button.
    expect(screen.getByText('Delete?')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Yes' }))
    await waitFor(() => expect(screen.queryByText('Doomed')).not.toBeInTheDocument())
  })

  it('dismisses the confirmation without deleting when choosing No', async () => {
    mockMcp({ polling_list_polls: [makePoll({ title: 'Keep Me' })] })
    const { user } = renderWithRouter(<PollList />)
    await screen.findByText('Keep Me')
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(screen.getByRole('button', { name: 'No' }))
    expect(screen.queryByText('Delete?')).not.toBeInTheDocument()
    expect(screen.getByText('Keep Me')).toBeInTheDocument()
  })

  it('shows an error banner when deletion fails', async () => {
    mockMcp({
      polling_list_polls: [makePoll({ title: 'Stubborn' })],
      polling_delete_poll: new Error('nope'),
    })
    const { user } = renderWithRouter(<PollList />)
    await screen.findByText('Stubborn')
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(screen.getByRole('button', { name: 'Yes' }))
    expect(
      await screen.findByText(/MCP polling_delete_poll failed|nope/),
    ).toBeInTheDocument()
    // Poll stays in the list since deletion failed.
    expect(screen.getByText('Stubborn')).toBeInTheDocument()
  })
})

describe('PollList - header', () => {
  it('always renders the create-new-poll link', async () => {
    mockMcp({ polling_list_polls: [makePoll()] })
    renderWithRouter(<PollList />)
    await screen.findByText('Team Sync')
    const header = screen.getByRole('link', { name: 'Create New Poll' })
    expect(header).toHaveAttribute('href', '/ui/polls/new')
  })
})

afterEach(() => {
  clearCookies()
})

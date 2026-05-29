import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import {
  render,
  screen,
  userEvent,
  setAuthCookies,
  clearCookies,
  mockFetch,
  mockResponse,
} from '@/test/utils'
import PollResults from './PollResults'
import type { Poll, PollResults as PollResultsType, SlotAggregation } from '@/hooks/usePolls'

function mcpEnvelope(payload: unknown) {
  return { result: { content: [{ text: JSON.stringify(payload) }] } }
}

function makePoll(overrides: Partial<Poll> = {}): Poll {
  return {
    id: 1,
    slug: 'results-me',
    title: 'Results Poll',
    description: 'A description',
    status: 'open',
    datetime_start: '2024-01-15T09:00:00.000Z',
    datetime_end: '2024-01-15T11:00:00.000Z',
    slot_duration_minutes: 60,
    response_count: 0,
    created_at: '2024-01-01T00:00:00.000Z',
    closes_at: null,
    finalized_at: null,
    finalized_time: null,
    ...overrides,
  }
}

function makeSlot(overrides: Partial<SlotAggregation> = {}): SlotAggregation {
  return {
    slot_start: '2024-01-15T09:00:00.000Z',
    slot_end: '2024-01-15T10:00:00.000Z',
    available_count: 3,
    if_needed_count: 1,
    total_count: 4,
    respondents: ['Alice', 'Bob', 'Carol'],
    ...overrides,
  }
}

function makeResults(overrides: Partial<PollResultsType> = {}): PollResultsType {
  return {
    poll: makePoll(),
    response_count: 0,
    aggregated: [],
    best_slots: [],
    ...overrides,
  }
}

interface ResultsMocks {
  results?: PollResultsType | Error
  upsertResult?: unknown | Error
  authed?: boolean
}

function mockResults({
  results = makeResults(),
  upsertResult = {},
  authed = false,
}: ResultsMocks = {}) {
  const jsonHeaders = { 'content-type': 'application/json' }
  return mockFetch(async (input) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url.includes('/auth/me')) {
      if (!authed) return mockResponse({ status: 401, json: { detail: 'no' } })
      return mockResponse({ json: { user_id: 1, scopes: ['*'] }, headers: jsonHeaders })
    }
    if (url.includes('polling_upsert_poll')) {
      if (upsertResult instanceof Error) {
        return mockResponse({ status: 500, json: { detail: upsertResult.message } })
      }
      return mockResponse({ json: mcpEnvelope(upsertResult) })
    }
    if (url.includes('/results')) {
      if (results instanceof Error) {
        return mockResponse({ status: 500, json: { detail: results.message }, headers: jsonHeaders })
      }
      return mockResponse({ json: results, headers: jsonHeaders })
    }
    return mockResponse({ status: 404, json: { detail: 'not found' }, headers: jsonHeaders })
  })
}

function renderResults(slug = 'results-me') {
  const user = userEvent.setup()
  render(
    <MemoryRouter initialEntries={[`/ui/polls/results/${slug}`]}>
      <Routes>
        <Route path="/ui/polls/results/:slug" element={<PollResults />} />
        <Route path="/ui/polls/edit/:slug" element={<div>Edit page</div>} />
        <Route path="/ui/polls/respond/:slug" element={<div>Respond page</div>} />
      </Routes>
    </MemoryRouter>,
  )
  return { user }
}

beforeEach(() => {
  clearCookies()
})

afterEach(() => {
  clearCookies()
})

describe('PollResults - loading and error', () => {
  it('shows a loading message before results resolve', () => {
    mockResults()
    renderResults()
    expect(screen.getByText('Loading results...')).toBeInTheDocument()
  })

  it('shows an error banner when results fail to load', async () => {
    mockResults({ results: new Error('results down') })
    renderResults()
    expect(await screen.findByText('results down')).toBeInTheDocument()
  })
})

describe('PollResults - rendering', () => {
  it('renders the title, status badge, and response count', async () => {
    mockResults({
      results: makeResults({ poll: makePoll({ title: 'Standup', status: 'open' }), response_count: 4 }),
    })
    renderResults()
    expect(await screen.findByRole('heading', { name: 'Standup' })).toBeInTheDocument()
    expect(screen.getByText('open')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
  })

  it('shows the empty respondents message when there are no responses', async () => {
    mockResults({ results: makeResults({ response_count: 0 }) })
    renderResults()
    expect(
      await screen.findByText('No responses yet. Be the first to add your availability!'),
    ).toBeInTheDocument()
  })

  it('renders the best-times list from best_slots', async () => {
    mockResults({
      results: makeResults({
        response_count: 4,
        aggregated: [makeSlot()],
        best_slots: [makeSlot({ available_count: 4, if_needed_count: 2 })],
      }),
    })
    renderResults()
    expect(await screen.findByText('Best Times')).toBeInTheDocument()
    expect(screen.getByText(/4 available/)).toBeInTheDocument()
    expect(screen.getByText(/\+2 if needed/)).toBeInTheDocument()
  })

  it('renders the finalized banner when finalized_time is set', async () => {
    mockResults({
      results: makeResults({
        poll: makePoll({ status: 'finalized', finalized_time: '2024-01-20T15:00:00.000Z' }),
      }),
    })
    renderResults()
    expect(await screen.findByText('Meeting Scheduled')).toBeInTheDocument()
  })

  it('shows the add-availability link only when the poll is open', async () => {
    mockResults({ results: makeResults({ poll: makePoll({ status: 'open' }) }) })
    renderResults()
    await screen.findByRole('heading', { name: 'Results Poll' })
    expect(
      screen.getByRole('link', { name: 'Add Your Availability' }),
    ).toHaveAttribute('href', '/ui/polls/respond/results-me')
  })

  it('hides the add-availability link when the poll is closed', async () => {
    mockResults({ results: makeResults({ poll: makePoll({ status: 'closed' }) }) })
    renderResults()
    await screen.findByRole('heading', { name: 'Results Poll' })
    expect(
      screen.queryByRole('link', { name: 'Add Your Availability' }),
    ).not.toBeInTheDocument()
  })
})

describe('PollResults - share link', () => {
  it('copies the respond URL to the clipboard', async () => {
    mockResults()
    const { user } = renderResults()
    await screen.findByRole('heading', { name: 'Results Poll' })
    // userEvent already provides a clipboard stub on navigator; spy on it.
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined)
    await user.click(screen.getByRole('button', { name: 'Copy Share Link' }))
    expect(writeText).toHaveBeenCalledWith(
      expect.stringContaining('/ui/polls/respond/results-me'),
    )
  })
})

describe('PollResults - management visibility', () => {
  it('hides the manage menu for unauthenticated users', async () => {
    mockResults({ authed: false })
    renderResults()
    await screen.findByRole('heading', { name: 'Results Poll' })
    expect(screen.queryByRole('button', { name: /Manage Poll/ })).not.toBeInTheDocument()
  })

  it('shows the manage menu for authenticated users', async () => {
    setAuthCookies()
    mockResults({ authed: true })
    renderResults()
    expect(
      await screen.findByRole('button', { name: /Manage Poll/ }),
    ).toBeInTheDocument()
  })
})

describe('PollResults - management actions (authenticated)', () => {
  beforeEach(() => {
    setAuthCookies()
  })

  it('shows Finalize and Close options for an open poll', async () => {
    mockResults({ authed: true, results: makeResults({ poll: makePoll({ status: 'open' }) }) })
    const { user } = renderResults()
    await user.click(await screen.findByRole('button', { name: /Manage Poll/ }))
    expect(screen.getByRole('button', { name: 'Finalize Poll' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Close Poll' })).toBeInTheDocument()
  })

  it('shows Reopen option for a closed poll', async () => {
    mockResults({ authed: true, results: makeResults({ poll: makePoll({ status: 'closed' }) }) })
    const { user } = renderResults()
    await user.click(await screen.findByRole('button', { name: /Manage Poll/ }))
    expect(screen.getByRole('button', { name: 'Reopen Poll' })).toBeInTheDocument()
  })

  it('closes the poll and updates the status badge', async () => {
    mockResults({
      authed: true,
      results: makeResults({ poll: makePoll({ status: 'open' }) }),
      upsertResult: { id: 1, status: 'closed' },
    })
    const { user } = renderResults()
    await user.click(await screen.findByRole('button', { name: /Manage Poll/ }))
    await user.click(screen.getByRole('button', { name: 'Close Poll' }))
    expect(await screen.findByText('closed')).toBeInTheDocument()
  })

  it('reopens a closed poll back to open', async () => {
    mockResults({
      authed: true,
      results: makeResults({ poll: makePoll({ status: 'closed' }) }),
      upsertResult: { id: 1, status: 'open' },
    })
    const { user } = renderResults()
    await user.click(await screen.findByRole('button', { name: /Manage Poll/ }))
    await user.click(screen.getByRole('button', { name: 'Reopen Poll' }))
    expect(await screen.findByText('open')).toBeInTheDocument()
  })

  it('shows an action error banner when a management call fails', async () => {
    mockResults({
      authed: true,
      results: makeResults({ poll: makePoll({ status: 'open' }) }),
      upsertResult: new Error('close failed'),
    })
    const { user } = renderResults()
    await user.click(await screen.findByRole('button', { name: /Manage Poll/ }))
    await user.click(screen.getByRole('button', { name: 'Close Poll' }))
    expect(
      await screen.findByText(/MCP polling_upsert_poll failed|close failed/),
    ).toBeInTheDocument()
  })
})

describe('PollResults - finalize modal', () => {
  beforeEach(() => {
    setAuthCookies()
  })

  it('lists best slots as radio options and disables finalize until one is chosen', async () => {
    mockResults({
      authed: true,
      results: makeResults({
        response_count: 4,
        aggregated: [makeSlot()],
        best_slots: [makeSlot({ available_count: 4 })],
      }),
    })
    const { user } = renderResults()
    await user.click(await screen.findByRole('button', { name: /Manage Poll/ }))
    await user.click(screen.getByRole('button', { name: 'Finalize Poll' }))
    expect(screen.getByText('Select a time to schedule the meeting:')).toBeInTheDocument()
    const finalizeBtn = screen.getByRole('button', { name: 'Finalize' })
    expect(finalizeBtn).toBeDisabled()
    await user.click(screen.getByRole('radio'))
    expect(finalizeBtn).toBeEnabled()
  })

  it('finalizes with the selected slot and shows the scheduled banner', async () => {
    mockResults({
      authed: true,
      results: makeResults({
        response_count: 4,
        aggregated: [makeSlot()],
        best_slots: [makeSlot({ available_count: 4 })],
      }),
      upsertResult: { id: 1, status: 'finalized' },
    })
    const { user } = renderResults()
    await user.click(await screen.findByRole('button', { name: /Manage Poll/ }))
    await user.click(screen.getByRole('button', { name: 'Finalize Poll' }))
    await user.click(screen.getByRole('radio'))
    await user.click(screen.getByRole('button', { name: 'Finalize' }))
    expect(await screen.findByText('Meeting Scheduled')).toBeInTheDocument()
  })

  it('shows the no-responses hint when there are no best slots', async () => {
    mockResults({
      authed: true,
      results: makeResults({ poll: makePoll({ status: 'open' }), best_slots: [] }),
    })
    const { user } = renderResults()
    await user.click(await screen.findByRole('button', { name: /Manage Poll/ }))
    await user.click(screen.getByRole('button', { name: 'Finalize Poll' }))
    expect(
      screen.getByText(/No responses yet. You can still finalize with a custom time./),
    ).toBeInTheDocument()
  })
})

describe('PollResults - cancel modal', () => {
  beforeEach(() => {
    setAuthCookies()
  })

  it('cancels the poll after confirming and shows the cancelled badge', async () => {
    mockResults({
      authed: true,
      results: makeResults({ poll: makePoll({ status: 'open' }) }),
      upsertResult: { id: 1, status: 'cancelled' },
    })
    const { user } = renderResults()
    await user.click(await screen.findByRole('button', { name: /Manage Poll/ }))
    await user.click(screen.getByRole('button', { name: 'Cancel Poll' }))
    // Confirmation modal appears.
    expect(screen.getByText(/Are you sure you want to cancel/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel Poll' }))
    expect(await screen.findByText('cancelled')).toBeInTheDocument()
  })

  it('keeps the poll when dismissing the cancel confirmation', async () => {
    mockResults({ authed: true, results: makeResults({ poll: makePoll({ status: 'open' }) }) })
    const { user } = renderResults()
    await user.click(await screen.findByRole('button', { name: /Manage Poll/ }))
    await user.click(screen.getByRole('button', { name: 'Cancel Poll' }))
    await user.click(screen.getByRole('button', { name: 'Keep Open' }))
    expect(screen.queryByText(/Are you sure you want to cancel/)).not.toBeInTheDocument()
    expect(screen.getByText('open')).toBeInTheDocument()
  })

  it('hides the cancel option for an already-cancelled poll', async () => {
    mockResults({ authed: true, results: makeResults({ poll: makePoll({ status: 'cancelled' }) }) })
    const { user } = renderResults()
    await user.click(await screen.findByRole('button', { name: /Manage Poll/ }))
    expect(screen.queryByRole('button', { name: 'Cancel Poll' })).not.toBeInTheDocument()
  })
})

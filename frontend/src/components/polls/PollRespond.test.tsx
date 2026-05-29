import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import {
  render,
  screen,
  fireEvent,
  userEvent,
  mockFetch,
  mockResponse,
} from '@/test/utils'
import PollRespond from './PollRespond'
import type { Poll, PollResults, ExistingResponse, SlotAggregation } from '@/hooks/usePolls'

function makePoll(overrides: Partial<Poll> = {}): Poll {
  return {
    id: 1,
    slug: 'respond-me',
    title: 'Respond Poll',
    description: 'Pick your slots',
    status: 'open',
    // Single day, two hourly slots for a predictable grid.
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

function makeResults(overrides: Partial<PollResults> = {}): PollResults {
  return {
    poll: makePoll(),
    response_count: 0,
    aggregated: [],
    best_slots: [],
    ...overrides,
  }
}

interface RespondMocks {
  poll?: Poll | Error
  results?: PollResults
  existing?: ExistingResponse | Error
  submitResult?: { response_id: number; edit_token: string; status: string } | Error
  updateResult?: { status: string } | Error
}

// Route public poll endpoints by method + path. The respond endpoint has
// several sub-paths sharing the /polls/respond/<slug> prefix, so order checks
// from most specific to least.
function mockRespond({
  poll = makePoll(),
  results = makeResults(),
  existing,
  submitResult = { response_id: 5, edit_token: 'tok-123', status: 'created' },
  updateResult = { status: 'updated' },
}: RespondMocks = {}) {
  const jsonHeaders = { 'content-type': 'application/json' }
  return mockFetch(async (input, init) => {
    const url = typeof input === 'string' ? input : input.toString()
    const method = (init?.method || 'GET').toUpperCase()

    if (method === 'PUT' && /\/polls\/respond\/[^/]+\/\d+/.test(url)) {
      if (updateResult instanceof Error) {
        return mockResponse({ status: 400, json: { detail: updateResult.message }, headers: jsonHeaders })
      }
      return mockResponse({ json: updateResult, headers: jsonHeaders })
    }
    if (method === 'POST' && url.includes('/polls/respond/')) {
      if (submitResult instanceof Error) {
        return mockResponse({ status: 400, json: { detail: submitResult.message }, headers: jsonHeaders })
      }
      return mockResponse({ json: submitResult, headers: jsonHeaders })
    }
    if (url.includes('/results')) {
      return mockResponse({ json: results, headers: jsonHeaders })
    }
    if (url.includes('/response')) {
      if (!existing || existing instanceof Error) {
        return mockResponse({ status: 404, json: { detail: 'invalid token' }, headers: jsonHeaders })
      }
      return mockResponse({ json: existing, headers: jsonHeaders })
    }
    if (url.includes('/polls/respond/')) {
      if (poll instanceof Error) {
        return mockResponse({ status: 404, json: { detail: poll.message }, headers: jsonHeaders })
      }
      return mockResponse({ json: poll, headers: jsonHeaders })
    }
    return mockResponse({ status: 404, json: { detail: 'not found' }, headers: jsonHeaders })
  })
}

function renderRespond(entry = '/ui/polls/respond/respond-me') {
  const user = userEvent.setup()
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/ui/polls/respond/:slug" element={<PollRespond />} />
      </Routes>
    </MemoryRouter>,
  )
  return { user }
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  localStorage.clear()
})

describe('PollRespond - loading and error states', () => {
  it('shows a loading message before the poll resolves', () => {
    mockRespond()
    renderRespond()
    expect(screen.getByText('Loading poll...')).toBeInTheDocument()
  })

  it('shows an error banner when the poll fails to load', async () => {
    mockRespond({ poll: new Error('No such poll') })
    renderRespond()
    expect(await screen.findByText('No such poll')).toBeInTheDocument()
  })
})

describe('PollRespond - closed poll', () => {
  it('shows the closed banner for a non-open poll', async () => {
    mockRespond({ poll: makePoll({ status: 'closed' }) })
    renderRespond()
    expect(
      await screen.findByText('This poll is no longer accepting responses.'),
    ).toBeInTheDocument()
  })

  it('shows the scheduled meeting time for a finalized poll', async () => {
    mockRespond({
      poll: makePoll({ status: 'finalized', finalized_time: '2024-01-20T15:00:00.000Z' }),
    })
    renderRespond()
    expect(await screen.findByText(/Meeting scheduled for:/)).toBeInTheDocument()
  })
})

describe('PollRespond - open poll form', () => {
  it('renders the title, name field, and availability grid', async () => {
    mockRespond()
    renderRespond()
    expect(await screen.findByRole('heading', { name: 'Respond Poll' })).toBeInTheDocument()
    expect(screen.getByLabelText(/Your Name/)).toBeInTheDocument()
    expect(screen.getByRole('grid', { name: 'Availability time grid' })).toBeInTheDocument()
  })

  it('disables submit until a name is entered', async () => {
    mockRespond()
    const { user } = renderRespond()
    const submit = await screen.findByRole('button', { name: 'Submit Availability' })
    expect(submit).toBeDisabled()
    await user.type(screen.getByLabelText(/Your Name/), 'Alice')
    expect(submit).toBeEnabled()
  })

  it('toggles the availability level buttons between available and if-needed', async () => {
    mockRespond()
    const { user } = renderRespond()
    await screen.findByRole('heading', { name: 'Respond Poll' })
    const ifNeeded = screen.getByRole('button', { name: 'If Needed' })
    await user.click(ifNeeded)
    // After selecting "If Needed", painting a slot marks it at level 2.
    const cell = screen.getByLabelText(/9:00 AM on .*: not selected/)
    fireEvent.mouseDown(cell)
    expect(
      await screen.findByLabelText(/9:00 AM on .*: selected as if needed/),
    ).toBeInTheDocument()
  })

  it('selecting a grid slot marks it as available', async () => {
    mockRespond()
    renderRespond()
    await screen.findByRole('heading', { name: 'Respond Poll' })
    const cell = screen.getByLabelText(/9:00 AM on .*: not selected/)
    fireEvent.mouseDown(cell)
    expect(
      await screen.findByLabelText(/9:00 AM on .*: selected as available/),
    ).toBeInTheDocument()
  })
})

describe('PollRespond - submission', () => {
  it('submits a new response and shows the success screen with an edit link', async () => {
    mockRespond()
    const { user } = renderRespond()
    await user.type(await screen.findByLabelText(/Your Name/), 'Bob')
    const cell = screen.getByLabelText(/9:00 AM on .*: not selected/)
    fireEvent.mouseDown(cell)
    await user.click(screen.getByRole('button', { name: 'Submit Availability' }))
    expect(await screen.findByText('Response submitted!')).toBeInTheDocument()
    const editInput = screen.getByDisplayValue(/\?edit=tok-123$/)
    expect(editInput).toBeInTheDocument()
  })

  it('shows an error banner when submission fails', async () => {
    mockRespond({ submitResult: new Error('save failed') })
    const { user } = renderRespond()
    await user.type(await screen.findByLabelText(/Your Name/), 'Carol')
    await user.click(screen.getByRole('button', { name: 'Submit Availability' }))
    expect(await screen.findByText('save failed')).toBeInTheDocument()
  })

  it('returns to the form when clicking Edit Response from the success screen', async () => {
    mockRespond()
    const { user } = renderRespond()
    await user.type(await screen.findByLabelText(/Your Name/), 'Dave')
    await user.click(screen.getByRole('button', { name: 'Submit Availability' }))
    await screen.findByText('Response submitted!')
    await user.click(screen.getByRole('button', { name: 'Edit Response' }))
    expect(
      await screen.findByRole('button', { name: 'Submit Availability' }),
    ).toBeInTheDocument()
  })
})

describe('PollRespond - edit mode via token', () => {
  const existing: ExistingResponse = {
    response_id: 9,
    respondent_name: 'Existing User',
    respondent_email: null,
    availabilities: [
      { slot_start: '2024-01-15T09:00:00.000Z', slot_end: '2024-01-15T10:00:00.000Z', availability_level: 1 },
    ],
  }

  it('prefills the form from an existing response when an edit token is in the URL', async () => {
    mockRespond({ existing })
    renderRespond('/ui/polls/respond/respond-me?edit=tok-xyz')
    expect(await screen.findByDisplayValue('Existing User')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Update Availability' }),
    ).toBeInTheDocument()
  })

  it('updates the existing response and shows the success screen', async () => {
    mockRespond({ existing })
    const { user } = renderRespond('/ui/polls/respond/respond-me?edit=tok-xyz')
    await screen.findByDisplayValue('Existing User')
    await user.click(screen.getByRole('button', { name: 'Update Availability' }))
    expect(await screen.findByText('Response submitted!')).toBeInTheDocument()
  })

  it('falls back to a fresh form when the edit token is invalid', async () => {
    mockRespond({ existing: new Error('bad token') })
    renderRespond('/ui/polls/respond/respond-me?edit=garbage')
    // No prefill, plain submit button (create mode).
    expect(
      await screen.findByRole('button', { name: 'Submit Availability' }),
    ).toBeInTheDocument()
  })
})

describe('PollRespond - results link', () => {
  it('shows a results link with the response count when responses exist', async () => {
    const agg: SlotAggregation[] = [
      {
        slot_start: '2024-01-15T09:00:00.000Z',
        slot_end: '2024-01-15T10:00:00.000Z',
        available_count: 2,
        if_needed_count: 0,
        total_count: 2,
        respondents: ['A', 'B'],
      },
    ]
    mockRespond({
      poll: makePoll({ response_count: 2 }),
      results: makeResults({ response_count: 2, aggregated: agg, poll: makePoll({ response_count: 2 }) }),
    })
    renderRespond()
    await screen.findByRole('heading', { name: 'Respond Poll' })
    expect(await screen.findByText(/2 responses so far/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'View full results' })).toHaveAttribute(
      'href',
      '/ui/polls/results/respond-me',
    )
  })
})

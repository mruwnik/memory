import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import {
  render,
  screen,
  waitFor,
  userEvent,
  setAuthCookies,
  clearCookies,
  mcpToolFromRequest,
  mockFetch,
  mockResponse,
} from '@/test/utils'
import PollCreate from './PollCreate'
import { mcpEnvelopeJson } from '@/hooks/mcpEnvelope.testhelper'

// upsert -> error toggle. Auth's /auth/me answered generically.
function mockMcp(upsertResult: unknown | Error) {
  return mockFetch(async (input, init) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url.includes('/auth/me')) {
      return mockResponse({ json: { user_id: 1, scopes: ['*'] } })
    }
    if (mcpToolFromRequest(input, init) === 'polling_upsert_poll') {
      if (upsertResult instanceof Error) {
        return mockResponse({ status: 500, json: { detail: upsertResult.message } })
      }
      return mockResponse({ json: mcpEnvelopeJson(upsertResult) })
    }
    return mockResponse({ status: 404, json: { detail: 'not found' } })
  })
}

// Render PollCreate with a results sentinel so navigation is observable.
function renderCreate() {
  const user = userEvent.setup()
  render(
    <MemoryRouter initialEntries={['/ui/polls/new']}>
      <Routes>
        <Route path="/ui/polls/new" element={<PollCreate />} />
        <Route path="/ui/polls/results/:slug" element={<div>Results for poll</div>} />
        <Route path="/ui/polls" element={<div>Polls list</div>} />
      </Routes>
    </MemoryRouter>,
  )
  return { user }
}

beforeEach(() => {
  setAuthCookies()
})

afterEach(() => {
  clearCookies()
})

describe('PollCreate - rendering', () => {
  it('renders the create form with default field values', () => {
    mockMcp({})
    renderCreate()
    expect(screen.getByRole('heading', { name: 'Create Availability Poll' })).toBeInTheDocument()
    expect(screen.getByLabelText(/Poll Title/)).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Create Poll' })).toBeInTheDocument()
  })

  it('disables submit until a title is entered', async () => {
    mockMcp({})
    const { user } = renderCreate()
    const submit = screen.getByRole('button', { name: 'Create Poll' })
    expect(submit).toBeDisabled()
    await user.type(screen.getByLabelText(/Poll Title/), 'Sprint Planning')
    expect(submit).toBeEnabled()
  })
})

describe('PollCreate - submission', () => {
  it('creates the poll and navigates to its results page on success', async () => {
    mockMcp({ slug: 'sprint-planning', id: 1 })
    const { user } = renderCreate()
    await user.type(screen.getByLabelText(/Poll Title/), 'Sprint Planning')
    await user.click(screen.getByRole('button', { name: 'Create Poll' }))
    expect(await screen.findByText('Results for poll')).toBeInTheDocument()
  })

  it('sends the entered title to the upsert call', async () => {
    const fetchMock = mockMcp({ slug: 'abc', id: 2 })
    const { user } = renderCreate()
    await user.type(screen.getByLabelText(/Poll Title/), 'Quarterly Review')
    await user.click(screen.getByRole('button', { name: 'Create Poll' }))
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([u, init]) => mcpToolFromRequest(u, init) === 'polling_upsert_poll',
      )
      expect(call).toBeTruthy()
      expect(String(call?.[1]?.body)).toContain('Quarterly Review')
    })
  })

  it('shows an error banner and stays on the form when creation fails', async () => {
    mockMcp(new Error('server exploded'))
    const { user } = renderCreate()
    await user.type(screen.getByLabelText(/Poll Title/), 'Doomed Poll')
    await user.click(screen.getByRole('button', { name: 'Create Poll' }))
    expect(
      await screen.findByText(/MCP polling_upsert_poll failed|server exploded/),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Create Availability Poll' })).toBeInTheDocument()
  })
})

describe('PollCreate - cancel', () => {
  it('navigates back to the poll list when cancel is clicked', async () => {
    mockMcp({})
    const { user } = renderCreate()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(await screen.findByText('Polls list')).toBeInTheDocument()
  })
})

describe('PollCreate - slot duration field', () => {
  it('lets the user pick a 60-minute slot duration', async () => {
    mockMcp({})
    const { user } = renderCreate()
    await user.selectOptions(screen.getByLabelText('Time Slot Duration'), '60')
    expect(screen.getByLabelText('Time Slot Duration')).toHaveValue('60')
  })
})

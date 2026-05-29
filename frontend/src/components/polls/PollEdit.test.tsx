import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import {
  render,
  screen,
  waitFor,
  userEvent,
  setAuthCookies,
  clearCookies,
  mockFetch,
  mockResponse,
} from '@/test/utils'
import PollEdit from './PollEdit'
import type { Poll } from '@/hooks/usePolls'
import { mcpEnvelopeJson } from '@/hooks/mcpEnvelope.testhelper'

function makePoll(overrides: Partial<Poll> = {}): Poll {
  return {
    id: 42,
    slug: 'edit-me',
    title: 'Editable Poll',
    description: 'Some description',
    status: 'open',
    datetime_start: '2024-01-15T09:00:00.000Z',
    datetime_end: '2024-01-15T17:00:00.000Z',
    slot_duration_minutes: 30,
    response_count: 0,
    created_at: '2024-01-01T00:00:00.000Z',
    closes_at: null,
    finalized_at: null,
    finalized_time: null,
    ...overrides,
  }
}

interface EditMocks {
  loadResult?: Poll | Error
  updateResult?: unknown | Error
}

function mockEdit({ loadResult = makePoll(), updateResult = {} }: EditMocks = {}) {
  return mockFetch(async (input) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url.includes('/auth/me')) {
      return mockResponse({ json: { user_id: 1, scopes: ['*'] } })
    }
    if (url.includes('polling_upsert_poll')) {
      if (updateResult instanceof Error) {
        return mockResponse({ status: 500, json: { detail: updateResult.message } })
      }
      return mockResponse({ json: mcpEnvelopeJson(updateResult) })
    }
    // Public load: GET /polls/respond/:slug
    if (url.includes('/polls/respond/')) {
      if (loadResult instanceof Error) {
        return mockResponse({
          status: 404,
          json: { detail: loadResult.message },
          headers: { 'content-type': 'application/json' },
        })
      }
      return mockResponse({
        json: loadResult,
        headers: { 'content-type': 'application/json' },
      })
    }
    return mockResponse({ status: 404, json: { detail: 'not found' } })
  })
}

function renderEdit(slug = 'edit-me') {
  const user = userEvent.setup()
  render(
    <MemoryRouter initialEntries={[`/ui/polls/edit/${slug}`]}>
      <Routes>
        <Route path="/ui/polls/edit/:slug" element={<PollEdit />} />
        <Route path="/ui/polls/results/:slug" element={<div>Results page</div>} />
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

describe('PollEdit - loading and error states', () => {
  it('shows a loading message before the poll resolves', () => {
    mockEdit()
    renderEdit()
    expect(screen.getByText('Loading poll...')).toBeInTheDocument()
  })

  it('shows an error and a back link when loading fails', async () => {
    mockEdit({ loadResult: new Error('Poll vanished') })
    renderEdit()
    expect(await screen.findByText('Poll vanished')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to Polls' })).toHaveAttribute(
      'href',
      '/ui/polls',
    )
  })
})

describe('PollEdit - populated form', () => {
  it('prefills the form with the loaded poll values', async () => {
    mockEdit({ loadResult: makePoll({ title: 'My Meeting', description: 'Agenda' }) })
    renderEdit()
    expect(await screen.findByDisplayValue('My Meeting')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Agenda')).toBeInTheDocument()
  })

  it('renders the status selector reflecting the current status', async () => {
    mockEdit({ loadResult: makePoll({ status: 'closed' }) })
    renderEdit()
    await screen.findByDisplayValue('Editable Poll')
    expect(screen.getByLabelText('Status')).toHaveValue('closed')
    expect(
      screen.getByText('No new responses allowed, but not finalized yet'),
    ).toBeInTheDocument()
  })

  it('enables the slot duration selector when there are no responses', async () => {
    mockEdit({ loadResult: makePoll({ response_count: 0 }) })
    renderEdit()
    await screen.findByDisplayValue('Editable Poll')
    expect(screen.getByLabelText('Time Slot Duration')).toBeEnabled()
  })

  it('disables the slot duration selector when responses exist', async () => {
    mockEdit({ loadResult: makePoll({ response_count: 3 }) })
    renderEdit()
    await screen.findByDisplayValue('Editable Poll')
    expect(screen.getByLabelText('Time Slot Duration')).toBeDisabled()
    expect(
      screen.getByText('Cannot change slot duration when responses exist'),
    ).toBeInTheDocument()
  })
})

describe('PollEdit - submission', () => {
  it('updates the poll and navigates to results on success', async () => {
    mockEdit({ loadResult: makePoll(), updateResult: { id: 42, slug: 'edit-me' } })
    const { user } = renderEdit()
    const title = await screen.findByDisplayValue('Editable Poll')
    await user.clear(title)
    await user.type(title, 'Renamed Poll')
    await user.click(screen.getByRole('button', { name: 'Save Changes' }))
    expect(await screen.findByText('Results page')).toBeInTheDocument()
  })

  it('sends the updated title and poll_id to the upsert call', async () => {
    const fetchMock = mockEdit({ updateResult: { id: 42 } })
    const { user } = renderEdit()
    const title = await screen.findByDisplayValue('Editable Poll')
    await user.clear(title)
    await user.type(title, 'Patched Title')
    await user.click(screen.getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([u]) =>
        String(u).includes('polling_upsert_poll'),
      )
      expect(call).toBeTruthy()
      const body = String(call?.[1]?.body)
      expect(body).toContain('Patched Title')
      expect(body).toContain('"poll_id":42')
    })
  })

  it('shows an error banner and stays on the form when update fails', async () => {
    mockEdit({ updateResult: new Error('update rejected') })
    const { user } = renderEdit()
    await screen.findByDisplayValue('Editable Poll')
    await user.click(screen.getByRole('button', { name: 'Save Changes' }))
    expect(
      await screen.findByText(/MCP polling_upsert_poll failed|update rejected/),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Edit Poll' })).toBeInTheDocument()
  })

  it('disables save when the title is cleared', async () => {
    mockEdit()
    const { user } = renderEdit()
    const title = await screen.findByDisplayValue('Editable Poll')
    await user.clear(title)
    expect(screen.getByRole('button', { name: 'Save Changes' })).toBeDisabled()
  })
})

describe('PollEdit - cancel', () => {
  it('navigates to the results page when cancel is clicked', async () => {
    mockEdit()
    const { user } = renderEdit()
    await screen.findByDisplayValue('Editable Poll')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(await screen.findByText('Results page')).toBeInTheDocument()
  })
})

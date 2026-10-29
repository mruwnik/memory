import { describe, it, expect, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor, within, setAuthCookies, clearCookies, mockFetchRoutes } from '@/test/utils'
import { AuditLog } from './AuditLog'

const event = (id: number, fields: Record<string, unknown> = {}) => ({
  id,
  timestamp: '2026-10-28T10:00:00+00:00',
  actor_id: 1,
  actor_email: 'admin@example.com',
  action: 'mcp.call',
  target: 'notes_upsert',
  attributes: {},
  ...fields,
})

const users = [
  { id: 1, name: 'Admin', email: 'admin@example.com', user_type: 'human', scopes: ['*'], api_key_count: 0 },
  { id: 2, name: 'Other', email: 'other@example.com', user_type: 'human', scopes: [], api_key_count: 0 },
]

const routes = (events: unknown[], total = events.length) => ({
  '/auth/me': { json: { user_id: 1, name: 'Admin', email: 'admin@example.com', user_type: 'human', scopes: ['*'] } },
  '/audit': { json: { total, offset: 0, limit: 50, events } },
  '/users': { json: users },
})

const auditParams = (fetchMock: ReturnType<typeof mockFetchRoutes>) =>
  new URL(fetchMock.mock.calls.map((c) => String(c[0])).filter((u) => u.includes('/audit')).at(-1)!, 'http://x').searchParams

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('AuditLog', () => {
  it('shows who did what, falling back to the stored email for deleted users', async () => {
    mockFetchRoutes(routes([event(1), event(2, { actor_id: null, actor_email: 'gone@example.com', action: 'settings.restart', target: null })]))
    renderWithRouter(<AuditLog />)

    expect(await screen.findByText('notes_upsert')).toBeInTheDocument()
    await waitFor(() => expect(within(screen.getByRole('list')).getByText(/Admin/)).toBeInTheDocument())
    expect(within(screen.getByRole('list')).getByText('settings.restart')).toBeInTheDocument()
    expect(screen.getByText(/gone@example\.com/)).toBeInTheDocument()
  })

  it('names an event with no actor as the system', async () => {
    mockFetchRoutes(routes([event(1, { actor_id: null, actor_email: null })]))
    renderWithRouter(<AuditLog />)

    expect(await screen.findByText(/^System/)).toBeInTheDocument()
  })

  it('shows attributes behind a details toggle', async () => {
    mockFetchRoutes(routes([event(1, { attributes: { title: 'x' } })]))
    renderWithRouter(<AuditLog />)

    expect(await screen.findByText('Details')).toBeInTheDocument()
    expect(screen.getByText(/"title": "x"/)).toBeInTheDocument()
  })

  it('filters by action and actor, back on the first page', async () => {
    const fetchMock = mockFetchRoutes(routes([event(1)], 120))
    const { user } = renderWithRouter(<AuditLog />)

    await screen.findByText('notes_upsert')
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(auditParams(fetchMock).get('offset')).toBe('50'))

    await user.selectOptions(screen.getByLabelText('Action'), 'settings.update')
    await user.selectOptions(await screen.findByLabelText('Actor'), 'Other')

    await waitFor(() => expect(auditParams(fetchMock).get('actor_id')).toBe('2'))
    expect(auditParams(fetchMock).get('action')).toBe('settings.update')
    expect(auditParams(fetchMock).has('offset')).toBe(false)
  })

  it('explains an empty log', async () => {
    mockFetchRoutes(routes([]))
    renderWithRouter(<AuditLog />)
    expect(await screen.findByText(/No audit events yet/)).toBeInTheDocument()
  })

  it('shows the server reason when loading fails', async () => {
    mockFetchRoutes({ ...routes([]), '/audit': { status: 403, json: { detail: 'Admins only' } } })
    renderWithRouter(<AuditLog />)
    expect(await screen.findByText('Admins only')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})

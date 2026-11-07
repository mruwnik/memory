import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor, within } from '@/test/utils'
import { emptyTriggerConfig, type Subscription } from '@/hooks/useAgents'
import SubscriptionsPage from './SubscriptionsPage'

const agents = {
  listSubscriptions: vi.fn(),
  fetchSubscription: vi.fn(),
  upsertSubscription: vi.fn(),
  deleteSubscription: vi.fn(),
  cancelTimer: vi.fn(),
  listDefinitions: vi.fn(),
  listSystemAgents: vi.fn(),
  catalog: vi.fn(),
}

vi.mock('@/hooks/useAgents', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAgents')>()),
  useAgents: () => agents,
}))

const config = { ...emptyTriggerConfig(), filter: { ...emptyTriggerConfig().filter, tags_any: ['ai'] } }
const queued = {
  id: 7, name: 'pr reviews', kind: 'ingest', config, definition_id: null, handler: { queue: 'prs' }, enabled: false,
  state: { tripped: { reason: 'its key was revoked', at: '2026-01-01T00:00:00Z' } }, api_key_id: 4, via_api_key: true,
  api_key_name: 'laptop', max_per_hour: null, backlog_max: null, coalesce_mode: 'none', order_by: 'item', active_since: null,
  next_at: null, created_at: null, updated_at: null,
  activity: { pending: 3, oldest_pending_at: new Date().toISOString(), last: { status: 'done', at: new Date().toISOString(), run_id: 'r1', run_status: 'done' } },
} as Subscription

beforeEach(() => {
  agents.listSubscriptions.mockResolvedValue({ subscriptions: [queued], timers: [] })
  agents.listDefinitions.mockResolvedValue([])
  agents.listSystemAgents.mockResolvedValue([])
  agents.catalog.mockResolvedValue({ agents: [], trigger_source_types: [] })
  agents.upsertSubscription.mockResolvedValue({})
  agents.fetchSubscription.mockImplementation((_id: number, { pending }: { pending?: boolean }) => Promise.resolve({
    deliveries: pending
      ? [{ item_id: 11, status: 'pending', changes: [], coalesced: [], created_at: null, not_before: null, error: null, run_id: null, run_status: null }]
      : [{ item_id: 10, status: 'done', changes: ['new'], coalesced: [], created_at: null, not_before: null, error: null, run_id: 'r1', run_status: 'failed' }],
  }))
})

describe('SubscriptionsPage', () => {
  it('shows where a subscription came from, its backlog and why it tripped', async () => {
    renderWithRouter(<SubscriptionsPage />)

    const row = (await screen.findByRole('button', { name: 'pr reviews' })).closest('li')!
    expect(within(row).getByText('queue prs')).toBeInTheDocument()
    expect(within(row).getByText('Tripped')).toBeInTheDocument()
    expect(within(row).getByText('Set up by key laptop')).toBeInTheDocument()
    expect(within(row).getByText(/^3 waiting, oldest/)).toBeInTheDocument()
    expect(within(row).getByText(/its key was revoked/)).toBeInTheDocument()
  })

  it('resumes a tripped subscription from when it tripped', async () => {
    const { user } = renderWithRouter(<SubscriptionsPage />)

    await user.click(await screen.findByRole('button', { name: 'Resume from trip' }))

    await waitFor(() => expect(agents.upsertSubscription).toHaveBeenCalledWith({ subscription_id: 7, enabled: true, resume: true }))
  })

  it('lists waiting deliveries apart from the ones that ended, with their runs', async () => {
    const { user } = renderWithRouter(<SubscriptionsPage />)

    await user.click(await screen.findByRole('button', { name: 'pr reviews' }))

    const waiting = (await screen.findByText('Waiting (3)')).parentElement!.parentElement!
    const recent = screen.getByText('Recent deliveries').parentElement!.parentElement!
    expect(within(waiting).getByText('Item 11')).toBeInTheDocument()
    expect(within(waiting).queryByText('Item 10')).not.toBeInTheDocument()
    expect(within(recent).getByRole('link', { name: /run/ })).toHaveAttribute('href', '/agents/runs/r1')
    expect(within(recent).getByText('Failed')).toBeInTheDocument()
  })

  it('reloads an open history when a refresh changes the subscription', async () => {
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'pr reviews' }))
    await screen.findByText('Waiting (3)')
    const fetched = agents.fetchSubscription.mock.calls.length
    agents.listSubscriptions.mockResolvedValue({ subscriptions: [{ ...queued, activity: { ...queued.activity, pending: 4 } }], timers: [] })

    await user.click(screen.getByRole('button', { name: 'Refresh' }))

    await screen.findByText('Waiting (4)')
    await waitFor(() => expect(agents.fetchSubscription.mock.calls.length).toBe(fetched + 2))
  })

  it('refuses on create a name another subscription has', async () => {
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'New subscription' }))

    await user.type(screen.getByLabelText('Name'), 'pr reviews')

    expect(screen.getByText('You already have a subscription named pr reviews')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create' })).toBeDisabled()
  })
})

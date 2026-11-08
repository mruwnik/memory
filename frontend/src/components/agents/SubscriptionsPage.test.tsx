import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor, within } from '@/test/utils'
import { emptyIngestConfig, type Subscription } from '@/hooks/useAgents'
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
  testSubscription: vi.fn(),
  recentItems: vi.fn(),
}

vi.mock('@/hooks/useWebhookLog', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useWebhookLog')>()),
  useWebhookLog: () => ({
    listAccounts: () => Promise.resolve([
      { source: 'slack', account_id: 3, name: 'team app', last_event_id: null, last_event_at: null, own: true },
      { source: 'slack', account_id: 5, name: 'someone else', last_event_id: null, last_event_at: null, own: false },
      { source: 'discord', account_id: 4, name: 'bot', last_event_id: null, last_event_at: null, own: true },
    ]),
  }),
}))

vi.mock('@/hooks/useAgents', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAgents')>()),
  useAgents: () => agents,
}))

const config = { ...emptyIngestConfig(), filter: { ...emptyIngestConfig().filter, tags_any: ['ai'] } }
const queued = {
  id: 7, name: 'pr reviews', kind: 'ingest', config, definition_id: null, handler: { queue: 'prs' }, enabled: false,
  state: { tripped: { reason: 'its key was revoked', at: '2026-01-01T00:00:00Z' } }, api_key_id: 4, via_api_key: true,
  api_key_name: 'laptop', max_per_hour: null, backlog_max: null, coalesce_mode: 'none', order_by: 'item', active_since: null,
  next_at: null, created_at: null, updated_at: null,
  activity: { pending: 3, oldest_pending_at: new Date().toISOString(), last: { status: 'done', at: new Date().toISOString(), run_id: 'r1', run_status: 'done' } },
} as Subscription

// How many live regions would announce `element`'s text.
const liveRegions = (element: HTMLElement) => {
  let count = 0
  for (let e: HTMLElement | null = element; e; e = e.parentElement) {
    if (e.matches('[role="alert"], [role="status"], [aria-live]')) count += 1
  }
  return count
}

beforeEach(() => {
  agents.listSubscriptions.mockResolvedValue({ subscriptions: [queued], timers: [] })
  agents.listDefinitions.mockResolvedValue([])
  agents.listSystemAgents.mockResolvedValue([])
  agents.catalog.mockResolvedValue({ agents: [], subscription_source_types: [] })
  agents.upsertSubscription.mockResolvedValue({})
  agents.recentItems.mockResolvedValue([{ id: 10, title: 'Release notes', modality: 'blog' }])
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

    await user.click(await screen.findByRole('button', { name: 'Resume from trip pr reviews' }))

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

    expect(screen.getByLabelText('Name')).toHaveAccessibleDescription(/You already have a subscription named pr reviews/)
    const create = screen.getByRole('button', { name: 'Create' })
    expect(create).toBeDisabled()
    // By the buttons too, since a long form's Name is scrolled out of view, but not announced twice.
    expect(create).toHaveAccessibleDescription('Name: You already have a subscription named pr reviews')
    expect(liveRegions(screen.getByText('Name: You already have a subscription named pr reviews'))).toBe(0)
  })

  it('creates a webhook subscription on one of my accounts, handed to a queue', async () => {
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'New subscription' }))

    // The page's own source filter comes first.
    await user.click(screen.getAllByRole('button', { name: 'Webhooks' }).at(-1)!)
    await user.selectOptions(screen.getByLabelText('Sent by'), 'slack')
    await user.selectOptions(screen.getByLabelText('Account'), await screen.findByRole('option', { name: 'team app' }))
    await user.type(screen.getByLabelText(/Event types/), 'reaction_added')
    await user.click(screen.getByLabelText('Stored items'))
    await user.type(screen.getByLabelText('Queue'), 'hooks')
    await user.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(agents.upsertSubscription).toHaveBeenCalledWith({
      create_only: true,
      handler: { queue: 'hooks' },
      source: { raw: { source: 'slack', account_id: 3, types: ['reaction_added'], outcome: ['no_item_type'] } },
    }))
    expect(screen.queryByRole('option', { name: 'bot' })).not.toBeInTheDocument()
    // An admin sees other people's accounts in the log, but can't subscribe to them.
    expect(screen.queryByRole('option', { name: 'someone else' })).not.toBeInTheDocument()
  })

  it('says a webhook source with none of my accounts will never fire', async () => {
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'New subscription' }))

    await user.click(screen.getAllByRole('button', { name: 'Webhooks' }).at(-1)!)
    await user.selectOptions(screen.getByLabelText('Sent by'), 'transcripts')

    expect(await screen.findByText(/You have no Transcripts accounts/)).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'GitHub' })).toBeInTheDocument()
  })

  it('shows a refused save by the buttons, without the tool call prefix', async () => {
    agents.upsertSubscription.mockRejectedValue(new Error("Error calling tool 'upsert': unknown slack account 9"))
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'New subscription' }))
    await user.type(screen.getByLabelText('Queue'), 'hooks')
    await user.type(screen.getByLabelText('Any of these tags (comma-separated)'), 'ai')

    await user.click(screen.getByRole('button', { name: 'Create' }))

    const refusal = await screen.findByText('Unknown slack account 9')
    expect(refusal.closest('[data-form-error]')).not.toBeNull()
  })

  it('names a webhook delivery by its event and shows its run instead of a done badge', async () => {
    const raw = {
      ...queued, id: 8, name: 'hooks', kind: 'raw', handler: { queue: 'hooks' }, enabled: true, state: {},
      config: { raw: { source: 'github', account_id: 1, types: [], outcome: ['stored', 'no_item_type'] } },
    } as unknown as Subscription
    agents.listSubscriptions.mockResolvedValue({ subscriptions: [raw], timers: [] })
    agents.fetchSubscription.mockImplementation((_id: number, { pending }: { pending?: boolean }) => Promise.resolve({
      deliveries: pending ? [] : [{
        raw_event_id: 41, type: 'pull_request.closed', container: '42', account_id: 1, status: 'done', created_at: null,
        error: null, run_id: 'r2', run_status: 'done',
      }],
    }))
    const { user } = renderWithRouter(<SubscriptionsPage />)

    await user.click(await screen.findByRole('button', { name: 'hooks' }))

    const recent = (await screen.findByText('Recent deliveries')).parentElement!.parentElement!
    expect(await within(recent).findByText('pull_request.closed')).toBeInTheDocument()
    expect(within(recent).getByRole('link', { name: 'webhook log' })).toHaveAttribute('href', '/sources/webhook-log?account=github%3A1')
    expect(within(recent).getByRole('link', { name: /run/ })).toHaveAttribute('href', '/agents/runs/r2')
    expect(within(recent).queryByText('done')).not.toBeInTheDocument()
  })

  it('keeps a saved account the picker no longer lists', async () => {
    const raw = {
      ...queued, id: 8, name: 'hooks', kind: 'raw', handler: { queue: 'hooks' }, enabled: true, state: {},
      config: { raw: { source: 'slack', account_id: 99, types: [], outcome: ['stored', 'no_item_type'] } },
    } as unknown as Subscription
    agents.listSubscriptions.mockResolvedValue({ subscriptions: [raw], timers: [] })
    const { user } = renderWithRouter(<SubscriptionsPage />)

    await user.click(await screen.findByRole('button', { name: 'Edit hooks' }))

    const saved = await screen.findByRole('option', { name: 'Slack account 99 (not one of yours now)' })
    expect((saved as HTMLOptionElement).selected).toBe(true)
  })

  it('tries the unsaved filter on a recent item and names the check it fails', async () => {
    agents.testSubscription.mockResolvedValue({ subscription_id: 7, item_id: 10, matches: false, failed: 'tags_any' })
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'Edit pr reviews' }))
    await user.clear(screen.getByLabelText('Any of these tags (comma-separated)'))
    await user.type(screen.getByLabelText('Any of these tags (comma-separated)'), 'ml')

    await user.click(screen.getByText('Test on an item'))
    await user.click(screen.getByLabelText('Item'))
    await user.click(await screen.findByRole('option', { name: /Release notes/ }))
    await user.click(screen.getByRole('button', { name: 'Test' }))

    expect(await screen.findByText(/Would not match: it fails Any of these tags/)).toBeInTheDocument()
    expect(agents.testSubscription).toHaveBeenCalledWith({
      item_id: 10, subscription_id: 7, source: { ...config, filter: { ...config.filter, tags_any: ['ml'] } },
    })
  })

  it('tries a new filter with its handler on a pasted item id', async () => {
    agents.testSubscription.mockResolvedValue({ subscription_id: null, item_id: 42, matches: true, failed: null })
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'New subscription' }))
    await user.type(screen.getByLabelText('Queue'), 'q')

    await user.click(screen.getByText('Test on an item'))
    await user.type(screen.getByLabelText('Item'), '42')
    await user.click(screen.getByRole('button', { name: 'Test' }))

    expect(await screen.findByText('Would match.')).toBeInTheDocument()
    expect(agents.testSubscription).toHaveBeenCalledWith(expect.objectContaining({ item_id: 42, handler: { queue: 'q' } }))
  })

  it('offers no item test for a schedule', async () => {
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'New subscription' }))

    await user.click(screen.getAllByRole('button', { name: 'Schedule' }).at(-1)!)

    expect(screen.queryByText('Test on an item')).not.toBeInTheDocument()
  })

  // Outside act, as a browser delivers them: each keystroke commits before React's other pending work runs.
  it.each(['Name', 'Queue', 'Text contains any of (comma-separated)'])('keeps every character of a fast burst into %s', async label => {
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'New subscription' }))
    const input = screen.getByLabelText(label)
    const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    const text = 'abcdefghijklmnopqrstuvwxyz'.repeat(3)
    const errors: unknown[] = []
    const onError = (e: ErrorEvent) => errors.push(e.error)
    window.addEventListener('error', onError)
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', false)

    for (let i = 1; i <= text.length; i += 1) {
      setValue.call(input, text.slice(0, i))
      input.dispatchEvent(new InputEvent('input', { bubbles: true }))
    }
    await new Promise(resolve => setTimeout(resolve, 0))
    vi.unstubAllGlobals()
    window.removeEventListener('error', onError)
    await user.type(input, '!')

    expect(errors).toEqual([])
    expect(input).toHaveValue(`${text}!`)
  })

  it('tells assistive tech why Create is disabled', async () => {
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'New subscription' }))

    const create = screen.getByRole('button', { name: 'Create' })
    expect(create).toBeDisabled()
    expect(create).toHaveAccessibleDescription('Name the queue')
    expect(screen.getByText('Name the queue')).toHaveAttribute('aria-live', 'polite')

    await user.type(screen.getByLabelText('Queue'), 'q')
    expect(create).toHaveAccessibleDescription(/^Set at least one of types/)
    await user.type(screen.getByLabelText('Any of these tags (comma-separated)'), 'ai')

    expect(create).toBeEnabled()
    expect(create).not.toHaveAccessibleDescription()
  })

  it('says how resuming from the trip differs from resuming from now', async () => {
    renderWithRouter(<SubscriptionsPage />)

    expect(await screen.findByText(/Resume from trip also delivers what arrived while it was tripped/)).toBeInTheDocument()
  })

  it.each<[string, string[], boolean]>([
    ['Timer', ['the timer'], false],
    ['Webhooks', [], true],
    ['All sources', ['pr reviews', 'the timer'], false],
  ])('with source %s shows %o', async (filter, shown, nothing) => {
    const timer = { id: 3, kind: 'timer', fire_at: null, handler: { notify: { channel: 'email', target: 'a@b.c', message: 'hi' } }, input: 'the timer', result: null, created_at: null }
    agents.listSubscriptions.mockResolvedValue({ subscriptions: [queued], timers: [timer] })
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await screen.findByRole('button', { name: 'pr reviews' })

    await user.click(screen.getByRole('button', { name: filter }))

    expect(['pr reviews', 'the timer'].filter(t => screen.queryByText(new RegExp(t)))).toEqual(shown)
    expect(screen.queryByText('Nothing matches these filters.') !== null).toBe(nothing)
  })

  it('shows a refused name on the Name field and drops it once edited', async () => {
    agents.upsertSubscription.mockRejectedValue(new Error("Error calling tool 'upsert': you already have a subscription named 'x'"))
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'Edit pr reviews' }))

    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText('Not saved: see Name above.')).toBeInTheDocument()
    expect(screen.getByLabelText('Name')).toHaveAccessibleDescription("You already have a subscription named 'x'")
    await user.type(screen.getByLabelText('Name'), '2')
    expect(screen.queryByText('Not saved: see Name above.')).not.toBeInTheDocument()
  })

  it.each<[string, string, string]>([
    ['leaves quoted names alone', "you already have a subscription named 'pr-regex repos'", "You already have a subscription named 'pr-regex repos'"],
    ['names both fields by label', 'invalid filter source: on_changes needs on_edit: only edits are filtered by change',
      '“Only for these changes” needs “Deliver an item again when it changes”: only edits are filtered by change'],
  ])("a refused save's wording %s", async (_, refusal, shown) => {
    agents.upsertSubscription.mockRejectedValue(new Error(refusal))
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'Edit pr reviews' }))

    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findAllByText(shown)).not.toHaveLength(0)
  })

  it('keeps focus on a paused subscription and says it paused', async () => {
    agents.listSubscriptions.mockResolvedValue({ subscriptions: [{ ...queued, enabled: true, state: {} }], timers: [] })
    const { user } = renderWithRouter(<SubscriptionsPage />)

    await user.click(await screen.findByRole('button', { name: 'Pause pr reviews' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Paused pr reviews')
    expect(screen.getByRole('button', { name: 'Pause pr reviews' })).toHaveFocus()
  })

  it('offers only the handlers a source can go to, and clears filters that hide everything', async () => {
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await screen.findByRole('button', { name: 'pr reviews' })

    await user.click(screen.getByRole('button', { name: 'Webhooks' }))

    expect(screen.queryByRole('button', { name: 'Message' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    // The button is gone, so focus goes to the first row it revealed rather than the page.
    expect(screen.getByRole('button', { name: 'pr reviews' })).toHaveFocus()
  })

  it('moves focus to the next row once a deleted one is gone', async () => {
    const other = { ...queued, id: 9, name: 'other' } as Subscription
    agents.listSubscriptions.mockResolvedValue({ subscriptions: [queued, other], timers: [] })
    agents.deleteSubscription.mockImplementation(() => {
      agents.listSubscriptions.mockResolvedValue({ subscriptions: [other], timers: [] })
      return Promise.resolve({})
    })
    const { user } = renderWithRouter(<SubscriptionsPage />)
    const row = (await screen.findByRole('button', { name: 'pr reviews' })).closest('li')!

    await user.click(within(row).getByRole('button', { name: 'Delete pr reviews' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))

    await waitFor(() => expect(screen.queryByRole('button', { name: 'pr reviews' })).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'other' })).toHaveFocus()
  })

  it.each<[string, string, string]>([
    ['a cron error on a filter', 'invalid cron expression', 'Invalid cron expression'],
    ['a regex error on a filter', 'invalid filter source: regex does not compile: missing )', 'Not saved: see Regex above.'],
  ])('shows %s where it can be read', async (_, refusal, alert) => {
    agents.upsertSubscription.mockRejectedValue(new Error(refusal))
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'Edit pr reviews' }))

    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(document.querySelector('[data-form-error]')).toHaveTextContent(alert))
  })

  it("says a refused regex on the Regex field in the form's words, in one live region", async () => {
    agents.upsertSubscription.mockRejectedValue(new Error('invalid filter source: regex does not compile: missing ) at position 4'))
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'Edit pr reviews' }))

    await user.click(screen.getByRole('button', { name: 'Save' }))

    const alert = await screen.findByText('Not saved: see Regex above.')
    expect(liveRegions(alert)).toBe(1)
    expect(screen.getByLabelText('Regex')).toHaveAccessibleDescription('“Regex” does not compile: missing ) at position 4')
    expect(screen.getByLabelText('Regex')).toBeInvalid()
  })

  it("says a refused item test in the form's words, in one live region", async () => {
    agents.testSubscription.mockRejectedValue(new Error(
      'invalid filter source: set at least one of source_types, tags_any, regex or dms_to_me'))
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'New subscription' }))

    await user.click(screen.getByText('Test on an item'))
    await user.type(screen.getByLabelText('Item'), '42')
    await user.click(screen.getByRole('button', { name: 'Test' }))

    const refusal = await screen.findByText(
      'Set at least one of “Source types”, “Any of these tags”, “Regex” or “Direct messages to my bots”')
    expect(liveRegions(refusal)).toBe(1)
  })

  it('drops a failed test once another item is picked', async () => {
    agents.testSubscription.mockRejectedValue(new Error('Unknown item 99999999'))
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'New subscription' }))
    await user.click(screen.getByText('Test on an item'))
    await user.type(screen.getByLabelText('Item'), '99999999')
    await user.click(screen.getByRole('button', { name: 'Test' }))
    await screen.findByText('Unknown item 99999999')

    await user.clear(screen.getByLabelText('Item'))
    await user.type(screen.getByLabelText('Item'), '42')

    expect(screen.queryByText('Unknown item 99999999')).not.toBeInTheDocument()
  })

  it('keeps focus on Test while the test runs, and gives the verdict in a status region already there', async () => {
    let finish = (_: unknown) => {}
    agents.testSubscription.mockReturnValue(new Promise(resolve => { finish = resolve }))
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'New subscription' }))
    await user.click(screen.getByText('Test on an item'))
    await user.type(screen.getByLabelText('Item'), '42')
    const region = screen.getByRole('status', { name: 'Test result' })

    await user.click(screen.getByRole('button', { name: 'Test' }))
    await user.click(screen.getByRole('button', { name: 'Testing...' }))

    expect(agents.testSubscription).toHaveBeenCalledTimes(1)
    const test = screen.getByRole('button', { name: 'Testing...' })
    expect(test).toHaveFocus()
    expect(test).toHaveAttribute('aria-busy', 'true')
    finish({ subscription_id: null, item_id: 42, matches: true, failed: null })
    await waitFor(() => expect(region).toHaveTextContent('Would match.'))
    expect(liveRegions(screen.getByText('Would match.'))).toBe(1)
  })

  it('marks each invalid field with its own problem at once', async () => {
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'New subscription' }))
    await user.type(screen.getByLabelText('Queue'), 'q')
    await user.type(screen.getByLabelText('Any of these tags (comma-separated)'), 'ai')

    await user.click(screen.getByLabelText('Batch items together'))
    await user.clear(screen.getByLabelText('Window (seconds)'))
    await user.clear(screen.getByLabelText('Max items'))

    expect(screen.getByLabelText('Window (seconds)')).toBeInvalid()
    expect(screen.getByLabelText('Window (seconds)')).toHaveAccessibleDescription('The batch window must be 60 to 86400 seconds')
    expect(screen.getByLabelText('Max items')).toHaveAccessibleDescription('A batch must hold 1 to 200 items')
    expect(screen.getByRole('button', { name: 'Create' })).toHaveAccessibleDescription('The batch window must be 60 to 86400 seconds')
  })

  it('marks a bad cron on the Cron field once the field is left', async () => {
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'New subscription' }))
    await user.click(screen.getAllByRole('button', { name: 'Schedule' }).at(-1)!)

    await user.clear(screen.getByLabelText(/^Cron/))
    await user.type(screen.getByLabelText(/^Cron/), '99 9 * * *')
    expect(screen.getByLabelText(/^Cron/)).toBeValid()
    await user.tab()

    expect(screen.getByLabelText(/^Cron/)).toBeInvalid()
    expect(screen.getByLabelText(/^Cron/)).toHaveAccessibleDescription(/Cron minute must be 0 to 59/)
  })

  it('shows the name length against its limit once it nears it', async () => {
    const { user } = renderWithRouter(<SubscriptionsPage />)
    await user.click(await screen.findByRole('button', { name: 'New subscription' }))

    await user.click(screen.getByLabelText('Name'))
    await user.paste('x'.repeat(95))

    expect(screen.getByLabelText('Name')).toHaveAccessibleDescription(/95\/100 characters/)
  })
})

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderWithRouter, screen, waitFor, within } from '@/test/utils'
import type { WebhookEvent } from '@/hooks/useWebhookLog'
import WebhookLog, { eventMeta, mergeOlder, payloadNote } from './WebhookLog'

const listAccounts = vi.fn()
const listEvents = vi.fn()
const getEvent = vi.fn()

vi.mock('@/hooks/useWebhookLog', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useWebhookLog')>()),
  useWebhookLog: () => ({ listAccounts, listEvents, getEvent }),
}))

const event = (fields: Partial<WebhookEvent>): WebhookEvent => ({
  id: 1, created_at: '2026-11-07T09:00:00Z', outcome: 'stored', type: 'message', container: null, container_name: null,
  links_items: true, item_ids: [], triggered: [], payload: null, payload_withheld: false, ...fields,
})
const stored = event({
  id: 2, created_at: '2026-11-07T10:00:00Z', type: 'pull_request.synchronize', container: '990', container_name: 'acme/api',
  item_ids: [55], triggered: [{ subscription_id: 7, subscription: 'pr reviews', item_id: 55, status: 'done', run_id: 'r9' }],
})
const untracked = event({ id: 1, outcome: 'not_collected', type: null, links_items: false })
const labeled = event({ id: 3, outcome: 'no_item_type', type: 'label.created', links_items: false })

beforeEach(() => {
  listAccounts.mockResolvedValue([
    { source: 'slack', account_id: 3, name: 'Team', last_event_id: null, last_event_at: null },
    { source: 'github', account_id: 5, name: 'acme', last_event_id: 2, last_event_at: '2026-11-07T10:00:00Z' },
  ])
  listEvents.mockResolvedValue([stored, untracked])
})

describe('WebhookLog', () => {
  it('opens the account with the latest delivery, fetching it once', async () => {
    renderWithRouter(<WebhookLog />)

    await screen.findByText('pull_request.synchronize')
    expect(listEvents).toHaveBeenCalledTimes(1)
    expect(listEvents).toHaveBeenCalledWith('github', 5, { limit: 50, outcome: undefined, type: '' })
  })

  it('shows what a delivery stored, where, and what it triggered', async () => {
    renderWithRouter(<WebhookLog />)

    const row = (await screen.findByText('pull_request.synchronize')).closest('li')!
    expect(within(row).getByText('stored')).toBeInTheDocument()
    expect(within(row).getByText(/in acme\/api/)).toBeInTheDocument()
    expect(within(row).getByText(/stored as item 55/)).toBeInTheDocument()
    expect(within(row).getByText('pr reviews')).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'run' })).toHaveAttribute('href', '/agents/runs/r9')
  })

  it('shows an untracked delivery as nothing but its time and outcome', async () => {
    renderWithRouter(<WebhookLog />)

    const row = (await screen.findByText('Delivery')).closest('li')!
    const meta = row.querySelectorAll('.text-xs > span')
    expect(within(row).getByText('not collected')).toBeInTheDocument()
    expect(meta).toHaveLength(1)
    expect(within(row).queryByText('Triggered')).not.toBeInTheDocument()
    expect(within(row).queryByText('Payload')).not.toBeInTheDocument()
  })

  it('drops a response for an account no longer shown', async () => {
    let finishGithub: (events: WebhookEvent[]) => void = () => {}
    listEvents.mockImplementation((source: string) => source === 'github'
      ? new Promise(resolve => { finishGithub = resolve })
      : Promise.resolve([event({ id: 9 })]))
    const { user } = renderWithRouter(<WebhookLog />)

    await user.selectOptions(await screen.findByLabelText('Source'), 'slack:3')
    await screen.findByText('message')
    await act(async () => finishGithub([stored]))

    expect(screen.getByText('message')).toBeInTheDocument()
    expect(screen.queryByText('pull_request.synchronize')).not.toBeInTheDocument()
  })

  it('fetches a payload only when it is opened', async () => {
    listEvents.mockResolvedValue([labeled])
    getEvent.mockResolvedValue({ ...labeled, payload: { label: { name: 'bug' } } })
    const { user } = renderWithRouter(<WebhookLog />)

    const summary = await screen.findByText('Payload')
    expect(getEvent).not.toHaveBeenCalled()
    await user.click(summary)

    expect(await screen.findByText(/"name": "bug"/)).toBeInTheDocument()
    expect(getEvent).toHaveBeenCalledWith('github', 5, 3)
  })

  it('filters by outcome and type on the server', async () => {
    const { user } = renderWithRouter(<WebhookLog />)

    await user.selectOptions(await screen.findByLabelText('Outcome'), 'rejected')
    await user.type(screen.getByLabelText('Type'), 'pull_request')

    await waitFor(() => expect(listEvents).toHaveBeenLastCalledWith('github', 5, { limit: 50, outcome: 'rejected', type: 'pull_request' }))
  })

  it('says when the account in the URL is not one the viewer has', async () => {
    renderWithRouter(<WebhookLog />, { initialEntries: ['/?account=<b>spoof</b>'] })

    const notice = await screen.findByText(/The source in this link isn't one you can see, so GitHub · acme is shown/)
    expect(notice).not.toHaveTextContent('spoof')
  })
})

it.each([
  [{ type: '' }, 'Delivery'],
  [{ type: null }, 'Delivery'],
  [{ type: 'ping' }, 'ping'],
])('titles a delivery of type %j as %s', async (fields, title) => {
  listEvents.mockResolvedValue([event({ ...fields, outcome: 'not_collected' })])
  renderWithRouter(<WebhookLog />)

  expect(await screen.findByText(title)).toBeInTheDocument()
})

it.each([
  [{ container: '990', container_name: null }, 'in 990'],
  [{ links_items: true }, 'no item linked: still queued, nothing new, or one you can\'t read'],
  [{ links_items: false }, 'the items it updates aren\'t linked to deliveries'],
])('says of %j: %s', (fields, fact) => {
  expect(eventMeta(event(fields))).toContain(fact)
})

it.each([
  [undefined, 'This delivery is no longer in the log.'],
  [event({ payload: { a: 1 }, payload_withheld: true }), 'Withheld: you can\'t read what is stored from where it came.'],
  [event({ payload: null }), 'Not kept: it has expired.'],
  [event({ payload: { truncated: true, bytes: 300_000 } }), 'Not kept: too large (293 KB).'],
  [event({ payload: { truncated: true } }), 'Not kept: nested too deeply.'],
  [event({ payload: { truncated: true, issue: {} } }), null],
  [event({ payload: ['a'] }), null],
])('notes in place of payload %#: %s', (e, note) => {
  expect(payloadNote(e)).toBe(note)
})

it('adds an older page without repeating what is shown', () => {
  expect(mergeOlder([stored, untracked], [untracked, { ...untracked, id: 0 }]).map(e => e.id)).toEqual([2, 1, 0])
})

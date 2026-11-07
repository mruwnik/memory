import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderWithRouter, screen, within } from '@/test/utils'
import type { WebhookEvent } from '@/hooks/useWebhookLog'
import WebhookLog, { mergeOlder } from './WebhookLog'

const listAccounts = vi.fn()
const listEvents = vi.fn()

vi.mock('@/hooks/useWebhookLog', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useWebhookLog')>()),
  useWebhookLog: () => ({ listAccounts, listEvents }),
}))

const stored: WebhookEvent = {
  id: 2, created_at: '2026-11-07T10:00:00Z', outcome: 'stored', type: 'pull_request.synchronize', container: 'acme/api',
  item_ids: [55], triggered: [{ subscription_id: 7, subscription: 'pr reviews', item_id: 55, status: 'done', run_id: 'r9' }],
}
const untracked: WebhookEvent = {
  id: 1, created_at: '2026-11-07T09:00:00Z', outcome: 'not_collected', type: null, container: null, item_ids: [], triggered: [],
}

beforeEach(() => {
  listAccounts.mockResolvedValue([
    { source: 'slack', account_id: 3, name: 'Team', last_event_id: null, last_event_at: null },
    { source: 'github', account_id: 5, name: 'acme', last_event_id: 2, last_event_at: '2026-11-07T10:00:00Z' },
  ])
  listEvents.mockResolvedValue([stored, untracked])
})

describe('WebhookLog', () => {
  it('opens the account with the latest delivery', async () => {
    renderWithRouter(<WebhookLog />)

    await screen.findByText('pull_request.synchronize')
    expect(listEvents).toHaveBeenCalledWith('github', 5, undefined, 50)
  })

  it('shows what a delivery stored and what it triggered', async () => {
    renderWithRouter(<WebhookLog />)

    const row = (await screen.findByText('pull_request.synchronize')).closest('li')!
    expect(within(row).getByText('stored')).toBeInTheDocument()
    expect(within(row).getByText(/stored as item 55/)).toBeInTheDocument()
    expect(within(row).getByText('pr reviews')).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'run' })).toHaveAttribute('href', '/agents/runs/r9')
  })

  it('shows an untracked delivery as nothing but its time and outcome', async () => {
    renderWithRouter(<WebhookLog />)

    const row = (await screen.findByText('not collected')).closest('li')!
    const meta = row.querySelectorAll('.text-xs > span')
    expect(within(row).getByText('Delivery')).toBeInTheDocument()
    expect(meta).toHaveLength(1)
    expect(within(row).queryByText('Triggered')).not.toBeInTheDocument()
  })

  it('drops a response for an account no longer shown', async () => {
    let finishGithub: (events: WebhookEvent[]) => void = () => {}
    listEvents.mockImplementation((source: string) => source === 'github'
      ? new Promise(resolve => { finishGithub = resolve })
      : Promise.resolve([{ ...untracked, id: 9, outcome: 'stored', type: 'message' }]))
    const { user } = renderWithRouter(<WebhookLog />)

    await user.selectOptions(await screen.findByLabelText('Source'), 'slack:3')
    await screen.findByText('message')
    await act(async () => finishGithub([stored]))

    expect(screen.getByText('message')).toBeInTheDocument()
    expect(screen.queryByText('pull_request.synchronize')).not.toBeInTheDocument()
  })
})

it('adds an older page without repeating what is shown', () => {
  expect(mergeOlder([stored, untracked], [untracked, { ...untracked, id: 0 }]).map(e => e.id)).toEqual([2, 1, 0])
})

import { describe, it, expect } from 'vitest'
import { emptyTriggerConfig, type Subscription, type TriggerConfig } from '@/hooks/useAgents'
import { deliveryLabel, describeFilter, formFor, formProblem, newForm, subscriptionState, toUpsert, type SubscriptionForm } from './subscriptions'

const filterConfig = { ...emptyTriggerConfig(), filter: { ...emptyTriggerConfig().filter, tags_any: ['ai'] } }

const subscription = (overrides: Partial<Subscription> = {}): Subscription => ({
  id: 7, name: 'reviews', kind: 'ingest', config: filterConfig, definition_id: null, handler: { queue: 'prs' },
  enabled: true, state: {}, api_key_id: null, via_api_key: false, api_key_name: null, max_per_hour: null,
  backlog_max: null, coalesce_mode: 'none', order_by: 'item', active_since: null, next_at: null, created_at: null,
  updated_at: null, activity: { pending: 0, oldest_pending_at: null, last: null },
  ...overrides,
} as Subscription)

const form = (overrides: Partial<SubscriptionForm>): SubscriptionForm => ({ ...newForm(), ...overrides })

describe('toUpsert', () => {
  it.each<[string, Partial<SubscriptionForm>, object]>([
    ['a queue on items, with its shaping', { name: 'prs', queue: ' prs ', config: filterConfig, maxPerHour: '10', orderBy: 'thread' },
      { name: 'prs', handler: { queue: 'prs' }, source: filterConfig, max_per_hour: 10, coalesce: 'none', order_by: 'thread' }],
    ['an agent on items, which takes no order', { handler: 'agent', agent: '12', config: filterConfig, coalesce: 'latest' },
      { handler: { agent: 12 }, source: filterConfig, coalesce: 'latest' }],
    ['a prebuilt agent on a schedule', { source: 'cron', handler: 'agent', agent: 'system:echo', cron: '0 9 * * 1 ', input: 'go' },
      { handler: { agent: 'system:echo' }, source: { cron: '0 9 * * 1', input: 'go' } }],
    ['a message on a schedule, without an empty subject', { source: 'cron', handler: 'notify', notify: { channel: 'slack', target: 'C1', message: 'hi', subject: ' ' } },
      { handler: { notify: { channel: 'slack', target: 'C1', message: 'hi' } }, source: { cron: '0 9 * * *' } }],
    ['a timer, which has no name', { name: 'ignored', source: 'at', handler: 'notify', at: '2030-01-01T09:00', notify: { channel: 'email', target: 'a@b.c', message: 'hi', subject: 'Ping' } },
      { handler: { notify: { channel: 'email', target: 'a@b.c', message: 'hi', subject: 'Ping' } }, source: { at: '2030-01-01T09:00' } }],
  ])('builds %s, refusing to overwrite one already so named', (_, fields, expected) => {
    expect(toUpsert(form(fields))).toEqual({ ...expected, create_only: true })
  })

  it('names the subscription it updates and sends the name only when it changed', () => {
    const existing = subscription()

    expect(toUpsert(formFor(existing), existing)).toEqual({
      subscription_id: 7, handler: { queue: 'prs' }, source: formFor(existing).config, coalesce: 'none', order_by: 'item',
      max_per_hour: 0, backlog_max: 0,
    })
    expect(toUpsert({ ...formFor(existing), name: 'renamed' }, existing)).toMatchObject({ subscription_id: 7, name: 'renamed' })
  })
})

it('clears an emptied cap and subject when updating', () => {
  const capped = subscription({ max_per_hour: 5 })
  const notify = { channel: 'email' as const, target: 'a@b.c', message: 'hi', subject: 'Ping' }
  const pinged = subscription({ kind: 'cron', config: { cron: '0 9 * * *', input: '' }, handler: { notify } } as Partial<Subscription>)

  expect(toUpsert({ ...formFor(capped), maxPerHour: '' }, capped)).toMatchObject({ max_per_hour: 0 })
  expect(toUpsert({ ...formFor(pinged), notify: { ...notify, subject: '' } }, pinged).handler).toEqual({ notify: { ...notify, subject: '' } })
})

describe('formProblem', () => {
  it.each<[Partial<SubscriptionForm>, string | null]>([
    [{ queue: 'prs' }, null],
    [{ queue: ' ' }, 'Name the queue'],
    [{ handler: 'notify' }, "Items can't go to a message"],
    [{ source: 'cron', handler: 'agent', agent: '3', cron: '0 9 * *', input: 'x' }, 'Use five cron fields: minute hour day-of-month month day-of-week'],
    [{ source: 'cron', handler: 'agent', agent: '3', input: ' ' }, 'Say what each run should do'],
    [{ source: 'at', handler: 'notify', notify: { channel: 'email', target: 'a', message: 'b' } }, 'Pick when the timer fires'],
    [{ queue: 'prs', maxPerHour: '1.5' }, 'Deliveries per hour must be a whole number'],
    [{ queue: 'prs', backlogMax: 'lots' }, 'Most waiting must be a whole number'],
    [{ queue: 'prs', maxPerHour: ' 10 ', backlogMax: '' }, null],
  ])('%o: %s', (fields, problem) => {
    expect(formProblem(form(fields))).toBe(problem)
  })

  it('refuses a name another subscription has', () => {
    expect(formProblem(form({ name: ' prs ', queue: 'q' }), ['prs'])).toBe('You already have a subscription named prs')
  })
})

describe('subscriptionState', () => {
  it.each<[Partial<Subscription>, string | null]>([
    [{}, null],
    [{ enabled: false }, 'Paused'],
    [{ enabled: false, state: { tripped: { reason: 'key revoked', at: 'x' } } }, 'Tripped'],
    [{ state: { throttled: { reason: 'rate', since: 'x', last: 'x' } } }, 'Throttled'],
  ])('%o is %s', (overrides, label) => {
    expect(subscriptionState(subscription(overrides))?.label ?? null).toBe(label)
  })
})

it('summarises a filter and its delivery', () => {
  const config = { ...filterConfig, delivery: { mode: 'batch' as const, window_sec: 600, max_items: 20 }, on_edit: true, on_changes: ['commits'] }

  expect(describeFilter(config)).toBe('tags: ai · batched (20 items or 600s) · also on commits')
})

it('summarises a stored filter that predates newer fields', () => {
  const stored = { delivery: { mode: 'per_item' }, template: 't', filter: { tags_any: ['ai'] } } as unknown as TriggerConfig

  expect(describeFilter(stored)).toBe('tags: ai · one delivery per item')
})

it.each([['skipped:shed:rate', 'skipped (shed: rate)'], ['done', 'done']])('labels %s as %s', (status, label) => {
  expect(deliveryLabel(status)).toBe(label)
})

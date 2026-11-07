import {
  emptyFilter, emptyTriggerConfig, type Handler, type NotifyHandler, type Subscription, type SubscriptionUpsert, type Timer, type TriggerConfig,
} from '@/hooks/useAgents'
import type { Tone } from '@/components/shared/ui'

export type SourceKind = 'filter' | 'cron' | 'at'
export type HandlerKind = 'agent' | 'queue' | 'notify'

export const SOURCE_LABELS: Record<SourceKind, string> = { filter: 'Items', cron: 'Schedule', at: 'Timer' }
export const HANDLER_LABELS: Record<HandlerKind, string> = { agent: 'Agent', queue: 'Queue', notify: 'Message' }
// What each source can be handed to (subscription_upsert's check_combination).
export const HANDLERS_FOR: Record<SourceKind, HandlerKind[]> = {
  filter: ['agent', 'queue'],
  cron: ['agent', 'notify'],
  at: ['agent', 'notify'],
}

export const DELIVERY_TONES: Record<string, Tone> = {
  pending: 'info',
  done: 'success',
  sent: 'success',
  failed: 'danger',
  expired: 'warning',
}
// Skipped deliveries are neutral: each says why.
export const deliveryTone = (status: string | null): Tone => (status && DELIVERY_TONES[status]) || 'neutral'
/** "skipped:shed:rate" reads "skipped (shed: rate)". */
export const deliveryLabel = (status: string) => {
  const [head, ...reason] = status.split(':')
  return reason.length ? `${head} (${reason.join(': ')})` : head
}

export const handlerKind = (handler: Handler): HandlerKind =>
  'queue' in handler ? 'queue' : 'notify' in handler ? 'notify' : 'agent'
export const sourceKind = (s: Subscription): SourceKind => (s.kind === 'cron' ? 'cron' : 'filter')

export const describeHandler = (handler: Handler, agentNames: Record<string, string> = {}) => {
  if ('queue' in handler) return `queue ${handler.queue}`
  if ('notify' in handler) return `${handler.notify.channel} to ${handler.notify.target}`
  return `agent ${agentNames[String(handler.agent)] ?? handler.agent}`
}

const list = (label: string, values: string[]) => values.length > 0 && `${label}: ${values.join(', ')}`

export const describeFilter = (config: TriggerConfig): string => {
  // Stored filters may predate fields added since.
  const f = { ...emptyFilter(), ...config.filter }
  const parts = [
    list('types', f.source_types),
    list('tags', f.tags_any),
    list('repos', f.repos),
    list('channels', f.channel_ids),
    list('not in', f.exclude_channel_ids),
    f.text_contains.length > 0 && `text: ${f.text_contains.map(t => `"${t}"`).join(' or ')}`,
    f.regex && `regex: /${f.regex}/`,
    list('by', f.authors_any),
    list('not by', f.exclude_authors),
    list('changed by', f.changed_by_any),
    list('not changed by', f.exclude_changed_by),
    f.mentions_me && 'mentions me',
    f.threads_im_in && "threads I'm in",
    f.dms_to_me && 'DMs to me',
    f.known_people_only && 'known people only',
  ].filter(Boolean)
  const delivery = config.delivery.mode === 'batch'
    ? `batched (${config.delivery.max_items} items or ${config.delivery.window_sec}s)`
    : 'one delivery per item'
  const edits = config.on_edit && (config.on_changes?.length ? `also on ${config.on_changes.join(', ')}` : 'also on edits')
  return [parts.join('; '), delivery, edits].filter(Boolean).join(' · ')
}

/** The off or held-back state worth a badge; an active subscription has none. */
export const subscriptionState = (s: Subscription): { label: string; tone: Tone } | null => {
  if (s.state.tripped) return { label: 'Tripped', tone: 'danger' }
  if (!s.enabled) return { label: 'Paused', tone: 'neutral' }
  if (s.state.throttled) return { label: 'Throttled', tone: 'warning' }
  return null
}

export const origin = (s: Subscription) =>
  s.via_api_key ? (s.api_key_name ? `Set up by key ${s.api_key_name}` : 'Set up by a deleted key') : 'Set up in the app'

export interface SubscriptionForm {
  name: string
  source: SourceKind
  handler: HandlerKind
  // A definition id (digits) or an agent's slug.
  agent: string
  queue: string
  notify: NotifyHandler
  config: TriggerConfig
  cron: string
  at: string
  input: string
  maxPerHour: string
  backlogMax: string
  coalesce: 'none' | 'latest'
  orderBy: 'none' | 'item' | 'thread'
}

const emptyNotify = (): NotifyHandler => ({ channel: 'email', target: '', message: '', subject: '' })

export const newForm = (agent?: string): SubscriptionForm => ({
  name: '',
  source: 'filter',
  handler: agent ? 'agent' : 'queue',
  agent: agent ?? '',
  queue: '',
  notify: emptyNotify(),
  config: emptyTriggerConfig(),
  cron: '0 9 * * *',
  at: '',
  input: '',
  maxPerHour: '',
  backlogMax: '',
  coalesce: 'none',
  orderBy: 'item',
})

export const formFor = (s: Subscription): SubscriptionForm => {
  const h = s.handler
  const base = newForm()
  return {
    ...base,
    name: s.name,
    source: sourceKind(s),
    handler: handlerKind(h),
    agent: 'agent' in h ? String(h.agent) : '',
    queue: 'queue' in h ? h.queue : '',
    notify: 'notify' in h ? { ...emptyNotify(), ...h.notify } : base.notify,
    // Older subscriptions may lack newer filter fields.
    config: s.kind === 'ingest'
      ? { ...base.config, ...s.config, filter: { ...base.config.filter, ...s.config.filter } }
      : base.config,
    cron: s.kind === 'cron' ? s.config.cron : base.cron,
    input: s.kind === 'cron' ? s.config.input ?? '' : '',
    maxPerHour: s.max_per_hour?.toString() ?? '',
    backlogMax: s.backlog_max?.toString() ?? '',
    coalesce: s.coalesce_mode,
    orderBy: s.order_by,
  }
}

const isCron = (cron: string) => cron.trim().split(/\s+/).length === 5
// Empty, for the default, or a whole number.
const isCount = (value: string) => /^\d*$/.test(value.trim())

/** What stops the form being sent, or null; the server checks the rest. */
export const formProblem = (form: SubscriptionForm, takenNames: string[] = []): string | null => {
  const name = form.name.trim()
  if (form.source !== 'at' && name && takenNames.includes(name)) return `You already have a subscription named ${name}`
  if (!HANDLERS_FOR[form.source].includes(form.handler)) return `${SOURCE_LABELS[form.source]} can't go to a ${HANDLER_LABELS[form.handler].toLowerCase()}`
  if (form.handler === 'agent' && !form.agent) return 'Pick an agent'
  if (form.handler === 'queue' && !form.queue.trim()) return 'Name the queue'
  if (form.handler === 'notify' && !(form.notify.target.trim() && form.notify.message.trim())) return 'A message needs a recipient and text'
  if (form.source === 'cron' && !isCron(form.cron)) return 'Use five cron fields: minute hour day-of-month month day-of-week'
  if (form.source === 'at' && !form.at) return 'Pick when the timer fires'
  if (form.source !== 'filter' && form.handler === 'agent' && !form.input.trim()) return 'Say what each run should do'
  if (form.source === 'filter' && !isCount(form.maxPerHour)) return 'Deliveries per hour must be a whole number'
  if (form.source === 'filter' && !isCount(form.backlogMax)) return 'Most waiting must be a whole number'
  return null
}

const agentRef = (agent: string): number | string => (/^\d+$/.test(agent) ? Number(agent) : agent)

// An update sends an empty subject, which clears the stored one.
const handlerOf = (form: SubscriptionForm, updating: boolean): Handler => {
  if (form.handler === 'queue') return { queue: form.queue.trim() }
  if (form.handler === 'notify') {
    const { subject, ...rest } = form.notify
    const kept = subject?.trim() ?? ''
    return { notify: { ...rest, ...(kept || updating ? { subject: kept } : {}) } }
  }
  return { agent: agentRef(form.agent) }
}

const count = (value: string) => (value.trim() ? Number(value) : undefined)

/** subscription_upsert's arguments for the form, creating or updating `existing`. */
export const toUpsert = (form: SubscriptionForm, existing?: Subscription): SubscriptionUpsert => {
  const runs = form.handler === 'agent'
  const source = form.source === 'filter'
    ? form.config
    : form.source === 'cron'
      ? { cron: form.cron.trim(), ...(runs ? { input: form.input } : {}) }
      : { at: form.at, ...(runs ? { input: form.input } : {}) }
  // On update, 0 clears a cap left empty.
  const cleared = existing ? 0 : undefined
  const shaping = form.source !== 'filter' ? {} : {
    max_per_hour: count(form.maxPerHour) ?? cleared,
    backlog_max: count(form.backlogMax) ?? cleared,
    coalesce: form.coalesce,
    ...(form.handler === 'queue' ? { order_by: form.orderBy } : {}),
  }
  const name = form.name.trim()
  return Object.fromEntries(Object.entries({
    ...(existing ? { subscription_id: existing.id } : { create_only: true }),
    ...(form.source !== 'at' && name && name !== existing?.name ? { name } : {}),
    handler: handlerOf(form, existing !== undefined),
    source,
    ...shaping,
  }).filter(([, v]) => v !== undefined)) as SubscriptionUpsert
}

export const describeTimer = (t: Timer, agentNames: Record<string, string> = {}) =>
  [describeHandler(t.handler, agentNames), t.input].filter(Boolean).join(': ')

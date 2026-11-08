import {
  emptyFilter, emptyIngestConfig, type Handler, type NotifyHandler, type RawConfig, type RawOutcome, type RawSource,
  type Subscription, type SubscriptionUpsert, type Timer, type IngestConfig,
} from '@/hooks/useAgents'
import type { Tone } from '@/components/shared/ui'
import { WEBHOOK_SOURCE_NAMES } from '@/hooks/useWebhookLog'
import { formatRelativeTime } from '@/components/sources/shared'

export type SourceKind = 'filter' | 'raw' | 'cron' | 'at'
export type HandlerKind = 'agent' | 'queue' | 'notify'

export const SOURCE_LABELS: Record<SourceKind, string> = { filter: 'Items', raw: 'Webhooks', cron: 'Schedule', at: 'Timer' }
export const HANDLER_LABELS: Record<HandlerKind, string> = { agent: 'Agent', queue: 'Queue', notify: 'Message' }
// What each source can be handed to (subscription_upsert's check_combination).
export const HANDLERS_FOR: Record<SourceKind, HandlerKind[]> = {
  filter: ['agent', 'queue'],
  // A webhook delivery is rendered when a worker claims it.
  raw: ['queue'],
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
export const sourceKind = (s: Subscription): SourceKind => (s.kind === 'ingest' ? 'filter' : s.kind)

/** formatRelativeTime, which can sit mid-sentence. */
export const ago = (at: string | null) => {
  const text = formatRelativeTime(at)
  return text === 'Just now' ? 'just now' : text
}

export const RAW_OUTCOME_LABELS: Record<RawOutcome, string> = { stored: 'stored items', no_item_type: 'stored nothing' }

/** `accountNames` by "source:account id". */
export const describeRaw = ({ source, account_id, types, outcome }: RawConfig['raw'], accountNames: Record<string, string> = {}): string =>
  [
    account_id === null
      ? `every ${WEBHOOK_SOURCE_NAMES[source]} account`
      : `${WEBHOOK_SOURCE_NAMES[source]} ${accountNames[`${source}:${account_id}`] ?? `account ${account_id}`}`,
    types.length > 0 ? `types: ${types.join(', ')}` : 'any type',
    outcome.length === 1 && RAW_OUTCOME_LABELS[outcome[0]],
  ].filter(Boolean).join(' · ')

export const describeHandler = (handler: Handler, agentNames: Record<string, string> = {}) => {
  if ('queue' in handler) return `queue ${handler.queue}`
  if ('notify' in handler) return `${handler.notify.channel} to ${handler.notify.target}`
  return `agent ${agentNames[String(handler.agent)] ?? handler.agent}`
}

const list = (label: string, values: string[]) => values.length > 0 && `${label}: ${values.join(', ')}`

export const describeFilter = (config: IngestConfig): string => {
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
  config: IngestConfig
  cron: string
  at: string
  input: string
  maxPerHour: string
  backlogMax: string
  coalesce: 'none' | 'latest'
  orderBy: 'none' | 'item' | 'thread'
  // A webhook source: types as typed, comma-separated; no account means every one.
  raw: { source: RawSource; accountId: string; types: string; outcome: RawOutcome[] }
}

const emptyNotify = (): NotifyHandler => ({ channel: 'email', target: '', message: '', subject: '' })

export const newForm = (agent?: string): SubscriptionForm => ({
  name: '',
  source: 'filter',
  handler: agent ? 'agent' : 'queue',
  agent: agent ?? '',
  queue: '',
  notify: emptyNotify(),
  config: emptyIngestConfig(),
  cron: '0 9 * * *',
  at: '',
  input: '',
  maxPerHour: '',
  backlogMax: '',
  coalesce: 'none',
  orderBy: 'item',
  raw: { source: 'github', accountId: '', types: '', outcome: ['stored', 'no_item_type'] },
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
    raw: s.kind === 'raw'
      ? {
          source: s.config.raw.source,
          accountId: s.config.raw.account_id?.toString() ?? '',
          types: s.config.raw.types.join(', '),
          outcome: s.config.raw.outcome,
        }
      : base.raw,
  }
}

export const FILTER_REQUIRED = 'Set at least one of types, tags, repos, channels, text, regex, authors, changes by, mentions, threads or DMs'

// Sources whose deliveries take a rate and backlog cap.
const capped = (source: SourceKind) => source === 'filter' || source === 'raw'
const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
const DAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat']
const CRON_FIELDS: [name: string, min: number, max: number, names: string[]][] = [
  ['minute', 0, 59, []], ['hour', 0, 23, []], ['day-of-month', 1, 31, []], ['month', 1, 12, MONTHS], ['day-of-week', 0, 7, DAYS],
]

const cronProblem = (cron: string): string | null => {
  const fields = cron.trim().split(/\s+/)
  if (fields.length !== 5) return 'Use five cron fields: minute hour day-of-month month day-of-week'
  for (const [i, [name, min, max, names]] of CRON_FIELDS.entries()) {
    for (const part of fields[i].split(',')) {
      const [range, step, ...rest] = part.split('/')
      if (rest.length > 0 || (step !== undefined && !/^[1-9]\d*$/.test(step))) return `Cron ${name} steps must be whole numbers above 0`
      if (range === '*') continue
      const bounds = range.split('-')
      const fits = (v: string) => names.includes(v.toLowerCase()) || (/^\d+$/.test(v) && Number(v) >= min && Number(v) <= max)
        // L, W, # and ? are croniter's extensions, left to the server.
        || /[LW#?]/.test(v)
      if (bounds.length > 2 || !bounds.every(fits)) return `Cron ${name} must be ${min} to ${max}`
    }
  }
  return null
}

/** Why the regex may not compile, or null. The browser's engine is close to the server's once Python's
 * spellings of flags and named groups are translated; the server has the last word. */
export const regexProblem = (regex: string | null): string | null => {
  if (!regex) return null
  const js = regex.replace(/^\(\?[aiLmsux]+\)/, '').replace(/\(\?P</g, '(?<').replace(/\(\?P=(\w+)\)/g, '\\k<$1>')
  try {
    new RegExp(js)
    return null
  } catch (e) {
    return `This regex may not compile: ${(e as Error).message}`
  }
}
const between = (n: number, min: number, max: number) => Number.isInteger(n) && n >= min && n <= max
// Empty, for the default, or a whole number.
const isCount = (value: string) => /^\d*$/.test(value.trim())

const narrows = (f: IngestConfig['filter']) =>
  [f.source_types, f.tags_any, f.channel_ids, f.text_contains, f.repos, f.authors_any, f.changed_by_any].some(v => v.length > 0)
  || Boolean(f.regex) || f.mentions_me || f.threads_im_in || f.dms_to_me

/** The wall clock in `timezone`, as a datetime-local input writes it. */
export const wallClock = (timezone: string, at = new Date()) =>
  new Intl.DateTimeFormat('sv-SE', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    .format(at).replace(' ', 'T')

export type ProblemField = 'cron' | 'maxPerHour' | 'backlogMax' | 'window' | 'maxItems'

/** Each field's own problem, so every field in error says so at once. */
export const fieldProblems = (form: SubscriptionForm): Partial<Record<ProblemField, string>> => {
  const delivery = form.config.delivery
  const batch = form.source === 'filter' && delivery.mode === 'batch' ? delivery : null
  const problems: [ProblemField, string | null | false][] = [
    ['cron', form.source === 'cron' && cronProblem(form.cron)],
    ['maxPerHour', capped(form.source) && !isCount(form.maxPerHour) && 'Deliveries per hour must be a whole number'],
    ['backlogMax', capped(form.source) && !isCount(form.backlogMax) && 'Most waiting must be a whole number'],
    ['window', batch !== null && !between(batch.window_sec, 60, 86400) && 'The batch window must be 60 to 86400 seconds'],
    ['maxItems', batch !== null && !between(batch.max_items, 1, 200) && 'A batch must hold 1 to 200 items'],
  ]
  return Object.fromEntries(problems.filter(([, problem]) => problem))
}

/** What stops the form being sent, or null; the server checks the rest. `now` is wallClock() in the user's timezone. */
export const formProblem = (form: SubscriptionForm, takenNames: string[] = [], now?: string): string | null => {
  const name = form.name.trim()
  if (form.source !== 'at' && name && takenNames.includes(name)) return `You already have a subscription named ${name}`
  if (!HANDLERS_FOR[form.source].includes(form.handler)) return `${SOURCE_LABELS[form.source]} can't go to a ${HANDLER_LABELS[form.handler].toLowerCase()}`
  if (form.handler === 'agent' && !form.agent) return 'Pick an agent'
  if (form.handler === 'queue' && !form.queue.trim()) return 'Name the queue'
  if (form.handler === 'notify' && !(form.notify.target.trim() && form.notify.message.trim())) return 'A message needs a recipient and text'
  if (form.source === 'at' && !form.at) return 'Pick when the timer fires'
  if (form.source === 'at' && now && form.at <= now) return 'Pick a time in the future'
  if (form.source === 'filter' && !narrows(form.config.filter)) return FILTER_REQUIRED
  if (form.source === 'raw' && form.raw.outcome.length === 0) return 'Take deliveries that stored items, stored nothing, or both'
  if ((form.source === 'cron' || form.source === 'at') && form.handler === 'agent' && !form.input.trim()) return 'Say what each run should do'
  return Object.values(fieldProblems(form))[0] ?? null
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

const rawSource = (raw: SubscriptionForm['raw']): RawConfig['raw'] => ({
  source: raw.source,
  account_id: raw.accountId ? Number(raw.accountId) : null,
  types: raw.types.split(',').map(t => t.trim()).filter(Boolean),
  outcome: raw.outcome,
})

/** subscription_upsert's arguments for the form, creating or updating `existing`. */
export const toUpsert = (form: SubscriptionForm, existing?: Subscription): SubscriptionUpsert => {
  const runs = form.handler === 'agent'
  const source = form.source === 'filter'
    ? form.config
    : form.source === 'raw'
      ? { raw: rawSource(form.raw) }
      : form.source === 'cron'
        ? { cron: form.cron.trim(), ...(runs ? { input: form.input } : {}) }
        : { at: form.at, ...(runs ? { input: form.input } : {}) }
  // On update, 0 clears a cap left empty.
  const cleared = existing ? 0 : undefined
  const caps = { max_per_hour: count(form.maxPerHour) ?? cleared, backlog_max: count(form.backlogMax) ?? cleared }
  const shaping = form.source === 'raw' ? caps : form.source !== 'filter' ? {} : {
    ...caps,
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

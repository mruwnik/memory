import { useEffect, useId, useState } from 'react'
import {
  CHANGE_TAGS, NOTIFY_CHANNELS, RAW_SOURCES, useAgents, type IngestConfig, type IngestFilter, type NotifyChannel, type RawOutcome,
  type RawSource, type RecentItem, type Subscription, type SubscriptionTestResult, type SubscriptionUpsert,
} from '@/hooks/useAgents'
import { useAuth } from '@/hooks/useAuth'
import { useWebhookLog, WEBHOOK_SOURCE_NAMES, type WebhookAccount } from '@/hooks/useWebhookLog'
import {
  Button, Checkbox, Combobox, Disclosure, Field, FieldRow, Fieldset, FormActions, Input, MultiSelect, Notice, Select, Textarea, ToggleGroup,
  useSubmit,
} from '@/components/shared/ui'
import {
  fieldProblems, formFor, formProblem, HANDLER_LABELS, HANDLERS_FOR, newForm, RAW_OUTCOME_LABELS, regexProblem, SOURCE_LABELS, toUpsert, wallClock,
  type HandlerKind, type ProblemField, type SourceKind, type SubscriptionForm,
} from './subscriptions'

export interface AgentOption { value: string; label: string; group: string }

const splitList = (text: string) => text.split(',').map(s => s.trim()).filter(Boolean)
const joined = (values: string[] | undefined) => (values ?? []).join(', ')
// An emptied number field stays empty, for formProblem to refuse, rather than becoming 0.
const numberOrNaN = (text: string) => (text.trim() ? Number(text) : NaN)
const capitalised = (text: string) => text.charAt(0).toUpperCase() + text.slice(1)
// The server's limit on a subscription's name.
const MAX_NAME_CHARS = 100
const nameHint = (name: string, creating: boolean) => name.length >= MAX_NAME_CHARS - 20
  ? `${name.length}/${MAX_NAME_CHARS} characters`
  : creating ? 'Leave empty for a numbered name.' : undefined

type Problems = Partial<Record<ProblemField, string>>

type ErrorField = 'name' | 'regex' | 'cron'
// A refused save names what it refused; shown on that field rather than by the buttons.
// Each with the sources whose form shows that field; elsewhere the message stays by the buttons.
const FIELD_ERRORS: [ErrorField, RegExp, string, (source: SourceKind) => boolean][] = [
  ['name', /^(name |names like|you already have a subscription named)/i, 'Name', s => s !== 'at'],
  ['regex', /regex does not compile/i, 'Regex', s => s === 'filter'],
  ['cron', /^invalid cron (expression|source)|cron expressions are supported|cron interval/i, 'Cron', s => s === 'cron'],
]
const errorField = (error: string | null, source: SourceKind) =>
  FIELD_ERRORS.find(([, pattern, , shown]) => error && pattern.test(error) && shown(source))

type ListField = 'tags_any' | 'repos' | 'channel_ids' | 'exclude_channel_ids' | 'text_contains' | 'authors_any'
  | 'exclude_authors' | 'changed_by_any' | 'exclude_changed_by'
const LIST_FIELDS: [ListField, string][] = [
  ['tags_any', 'Any of these tags'],
  ['repos', 'GitHub repos (owner/name)'],
  ['channel_ids', 'Channel ids (Discord/Slack)'],
  ['exclude_channel_ids', 'Ignore channel ids'],
  ['text_contains', 'Text contains any of'],
  ['authors_any', 'Only by these authors'],
  ['exclude_authors', 'Ignore authors'],
  ['changed_by_any', 'Only changes by'],
  ['exclude_changed_by', 'Ignore changes by'],
]
type FlagField = 'mentions_me' | 'threads_im_in' | 'dms_to_me' | 'known_people_only'
const FLAGS: [FlagField, string][] = [
  ['mentions_me', 'Messages that mention my bots'],
  ['threads_im_in', "Threads I'm in"],
  ['dms_to_me', 'Direct messages to my bots'],
  ['known_people_only', 'Only authors who are known people'],
]
// The form's labels for the keys the server names fields by: subscription_test's `failed`, and refusals.
const FIELD_LABELS: Record<string, string> = Object.fromEntries([
  ['source_types', 'Source types'], ...LIST_FIELDS, ['regex', 'Regex'], ...FLAGS, ['exclude_own', 'Skip my own Slack and Discord messages, except DMs to my bots'],
  ['window_sec', 'Window (seconds)'], ['max_items', 'Max items'], ['on_edit', 'Deliver an item again when it changes'],
  ['on_changes', 'Only for these changes'],
])
// The server keeps its keys and an "invalid filter source:" lead-in for MCP callers. What it quotes
// (a name, a value) is the user's own text, so it is left alone.
const userWording = (message: string) => capitalised(message
  .replace(/^invalid \w+ (source|handler):\s*/i, '')
  .split(/('[^']*')/)
  .map((part, i) => i % 2 ? part : part.replace(/(?<![\w-])(?:filter\.|delivery\.)?([a-z]+(?:_[a-z]+)*)(?![\w-])/g,
    (key, name: string) => (FIELD_LABELS[name] ? `“${FIELD_LABELS[name]}”` : key)))
  .join(''))
const REASONS: Record<string, string> = {
  own_authors: 'one of our bots or syncs posted it',
  readable: "the subscription's credential can't read it",
  posted: 'one of its runs reported posting it',
  credentials: 'its key no longer works',
  config: 'its settings no longer validate',
  regex_timeout: 'the regex took too long on it, which counts as no match',
}
const verdict = ({ matches, failed }: SubscriptionTestResult) => {
  if (matches || !failed) return 'Would match.'
  return REASONS[failed] ? `Would not match: ${REASONS[failed]}.` : `Would not match: it fails ${FIELD_LABELS[failed] ?? failed}.`
}
const itemLabel = (i: RecentItem) => `${i.title || i.modality} (#${i.id})`

function ItemTest({ form, existing }: { form: SubscriptionForm; existing?: Subscription }) {
  const { testSubscription, recentItems } = useAgents()
  const [items, setItems] = useState<RecentItem[] | null>(null)
  const [query, setQuery] = useState('')
  const [itemId, setItemId] = useState('')
  const [result, setResult] = useState<SubscriptionTestResult | null>(null)
  const { busy, error, setError, submit } = useSubmit()
  // Fetched on first open: most edits never test.
  const open = (e: React.SyntheticEvent<HTMLDetailsElement>) => {
    if (e.currentTarget.open && items === null) recentItems().then(setItems).catch(() => setItems([]))
  }
  const pick = (value: string) => { setItemId(value); setResult(null); setError(null) }
  const run = () => submit(async () => {
    setResult(null)
    const { handler, source } = toUpsert(form, existing)
    const tried = existing ? { subscription_id: existing.id } : { handler }
    setResult(await testSubscription({ item_id: Number(itemId), source: source as IngestConfig, ...tried }))
  })
  const q = query.trim().toLowerCase()
  const options = (items ?? []).map(i => ({ value: String(i.id), label: itemLabel(i) })).filter(o => o.label.toLowerCase().includes(q))
  return (
    <Disclosure summary="Test on an item" onToggle={open}>
      <div className="space-y-3">
        <p className="text-sm text-slate-600">
          Whether this filter, as edited and unsaved, would take an item as it is now. Whether the event would count as new or
          changed isn't checked.
        </p>
        <Field label="Item" hint="Pick a recent item, or type an item id.">
          <Combobox allowCustom above value={itemId} options={options} loading={items === null}
            // A pick ends the search, so the list reopens whole and the pick keeps its label.
            onChange={value => { setQuery(''); pick(value) }}
            onQueryChange={text => { setQuery(text); pick(/^\d+$/.test(text.trim()) ? text.trim() : '') }} />
        </Field>
        {/* Busy rather than disabled, so a keyboard user's focus stays on it. */}
        <Button disabled={!itemId} aria-busy={busy} onClick={() => !busy && run()}>{busy ? 'Testing...' : 'Test'}</Button>
        {/* Always rendered: a status region inserted along with its text is often not read. */}
        <div role="status" aria-label="Test result">
          {result && <Notice role="none" tone={result.matches ? 'success' : 'warning'}>{verdict(result)}</Notice>}
        </div>
        {error && <Notice tone="error">{userWording(error)}</Notice>}
      </div>
    </Disclosure>
  )
}

function FilterFields({ form, update, sourceTypes, regexError, problems }: {
  form: SubscriptionForm
  update: (change: Partial<SubscriptionForm>) => void
  sourceTypes: string[]
  regexError?: string
  problems: Problems
}) {
  // Comma lists are edited as text and split on save, so a half-typed entry isn't lost.
  const [texts, setTexts] = useState<Record<ListField, string>>(
    () => Object.fromEntries(LIST_FIELDS.map(([k]) => [k, joined(form.config.filter[k])])) as Record<ListField, string>,
  )
  const config = form.config
  const setFilter = (change: Partial<IngestFilter>) => update({ config: { ...config, filter: { ...config.filter, ...change } } })
  const setText = (field: ListField, value: string) => {
    setTexts({ ...texts, [field]: value })
    setFilter({ [field]: splitList(value) })
  }
  const ownDefault = form.handler !== 'queue'
  const batch = config.delivery.mode === 'batch' ? config.delivery : null
  return (
    <>
      <Notice tone="info">
        Matches items as they are stored, whether a webhook or a sync brought them in. At least one of types, tags, repos,
        channels, text, regex, authors, changes by, mentions, threads or DMs is required.
      </Notice>
      <Field label="Source types" hint="None means any type.">
        <MultiSelect options={sourceTypes} value={config.filter.source_types} onChange={v => setFilter({ source_types: v })} placeholder="Any type" />
      </Field>
      <FieldRow>
        {LIST_FIELDS.map(([field, label]) => (
          <Field key={field} label={`${label} (comma-separated)`}>
            <Input value={texts[field]} onChange={e => setText(field, e.target.value)} />
          </Field>
        ))}
        <Field label="Regex" error={regexError ?? regexProblem(config.filter.regex) ?? undefined}>
          <Input className="font-mono" value={config.filter.regex ?? ''} onChange={e => setFilter({ regex: e.target.value.trim() || null })} />
        </Field>
      </FieldRow>
      <Fieldset legend="Only" className="flex flex-col">
        {FLAGS.map(([field, label]) => (
          <Checkbox key={field} label={label} checked={config.filter[field]} onChange={e => setFilter({ [field]: e.target.checked })} />
        ))}
        <Checkbox label="Skip my own Slack and Discord messages, except DMs to my bots" checked={config.filter.exclude_own ?? ownDefault}
          onChange={e => setFilter({ exclude_own: e.target.checked })} />
      </Fieldset>
      <Fieldset legend="Delivery" className="flex flex-col space-y-2">
        <Checkbox label="Batch items together" checked={config.delivery.mode === 'batch'}
          onChange={e => update({ config: { ...config, delivery: e.target.checked ? { mode: 'batch', window_sec: 3600, max_items: 50 } : { mode: 'per_item' } } })} />
        {batch && (
          <FieldRow>
            <Field label="Window (seconds)" error={problems.window}>
              <Input type="number" min="60" max="86400" value={Number.isNaN(batch.window_sec) ? '' : batch.window_sec}
                onChange={e => update({ config: { ...config, delivery: { ...batch, window_sec: numberOrNaN(e.target.value) } } })} />
            </Field>
            <Field label="Max items" error={problems.maxItems}>
              <Input type="number" min="1" max="200" value={Number.isNaN(batch.max_items) ? '' : batch.max_items}
                onChange={e => update({ config: { ...config, delivery: { ...batch, max_items: numberOrNaN(e.target.value) } } })} />
            </Field>
          </FieldRow>
        )}
        <Checkbox label="Deliver an item again when it changes" checked={config.on_edit}
          onChange={e => update({ config: { ...config, on_edit: e.target.checked, on_changes: e.target.checked ? config.on_changes : [] } })} />
        {config.on_edit && (
          <Field label="Only for these changes" hint="None means any change.">
            <MultiSelect options={[...CHANGE_TAGS]} value={config.on_changes} onChange={v => update({ config: { ...config, on_changes: v } })} placeholder="Any change" />
          </Field>
        )}
      </Fieldset>
      <Field label="Delivery text" hint="{items} becomes one line per item; text people wrote arrives marked as untrusted.">
        <Textarea className="h-20" value={config.template} onChange={e => update({ config: { ...config, template: e.target.value } })} />
      </Field>
      <FieldRow>
        <Field label="Deliveries per hour" hint="Beyond it they wait an hour, then are dropped." error={problems.maxPerHour}>
          <Input type="number" min="1" placeholder="Server default" value={form.maxPerHour} onChange={e => update({ maxPerHour: e.target.value })} />
        </Field>
        <Field label="Most waiting" hint="Beyond it the oldest are dropped." error={problems.backlogMax}>
          <Input type="number" min="1" placeholder="No limit (default)" value={form.backlogMax} onChange={e => update({ backlogMax: e.target.value })} />
        </Field>
        <Field label="A newer event for the same item">
          <Select value={form.coalesce} onChange={e => update({ coalesce: e.target.value as SubscriptionForm['coalesce'] })}>
            <option value="none">Is delivered as well</option>
            <option value="latest">Replaces the waiting one</option>
          </Select>
        </Field>
        {form.handler === 'queue' && (
          <Field label="Order deliveries by" hint="A later delivery with the same key waits for the earlier one.">
            <Select value={form.orderBy} onChange={e => update({ orderBy: e.target.value as SubscriptionForm['orderBy'] })}>
              <option value="item">Item</option>
              <option value="thread">Thread</option>
              <option value="none">Not ordered</option>
            </Select>
          </Field>
        )}
      </FieldRow>
    </>
  )
}

function RawFields({ form, update, locked, problems }: {
  form: SubscriptionForm
  update: (change: Partial<SubscriptionForm>) => void
  problems: Problems
  // A stored subscription keeps its source.
  locked: boolean
}) {
  const { listAccounts } = useWebhookLog()
  const [accounts, setAccounts] = useState<WebhookAccount[] | null>(null)
  useEffect(() => { listAccounts().then(setAccounts).catch(() => setAccounts([])) }, [listAccounts])
  const loaded = accounts !== null
  const raw = form.raw
  const setRaw = (change: Partial<SubscriptionForm['raw']>) => update({ raw: { ...raw, ...change } })
  const toggle = (outcome: RawOutcome, on: boolean) =>
    setRaw({ outcome: on ? [...raw.outcome, outcome] : raw.outcome.filter(o => o !== outcome) })
  // An admin's list also has other people's accounts, which can't carry the admin's subscriptions.
  const own = (accounts ?? []).filter(a => a.own && a.source === raw.source)
  const name = WEBHOOK_SOURCE_NAMES[raw.source]
  return (
    <>
      <Notice tone="info">
        Webhook deliveries to your own accounts, whether or not anything was stored. Deliveries from channels or repos that
        aren't collected never arrive. Anything that becomes an item is better taken as an Items source, which syncs also feed.
      </Notice>
      <FieldRow>
        <Field label="Sent by" hint={locked ? "A subscription's source can't change: delete it and create another." : undefined}>
          <Select value={raw.source} disabled={locked} onChange={e => setRaw({ source: e.target.value as RawSource, accountId: '' })}>
            {RAW_SOURCES.map(s => <option key={s} value={s}>{WEBHOOK_SOURCE_NAMES[s]}</option>)}
          </Select>
        </Field>
        <Field label="Account" hint={loaded && own.length === 0
          ? `You have no ${name} accounts, so this subscription will never fire.`
          : undefined}>
          <Select value={raw.accountId} onChange={e => setRaw({ accountId: e.target.value })}>
            <option value="">Every {name} account of mine</option>
            {/* A saved account no longer the caller's (or gone) stays chosen until they change it. */}
            {raw.accountId && !own.some(a => String(a.account_id) === raw.accountId) && (
              <option value={raw.accountId}>{`${name} account ${raw.accountId} (not one of yours now)`}</option>
            )}
            {own.map(a => <option key={a.account_id} value={String(a.account_id)}>{a.name}</option>)}
          </Select>
        </Field>
        <Field label="Event types (comma-separated)" hint="An event, or event.action, such as pull_request or reaction_added. None means any.">
          <Input value={raw.types} onChange={e => setRaw({ types: e.target.value })} />
        </Field>
      </FieldRow>
      <Fieldset legend="Deliveries that" className="flex flex-col">
        {(Object.keys(RAW_OUTCOME_LABELS) as RawOutcome[]).map(o => (
          <Checkbox key={o} label={capitalised(RAW_OUTCOME_LABELS[o])} checked={raw.outcome.includes(o)}
            onChange={e => toggle(o, e.target.checked)} />
        ))}
      </Fieldset>
      <FieldRow>
        <Field label="Deliveries per hour" hint="Beyond it they are dropped." error={problems.maxPerHour}>
          <Input type="number" min="1" placeholder="Server default" value={form.maxPerHour} onChange={e => update({ maxPerHour: e.target.value })} />
        </Field>
        <Field label="Most waiting" hint="Beyond it the oldest are dropped." error={problems.backlogMax}>
          <Input type="number" min="1" placeholder="1000 (default)" value={form.backlogMax} onChange={e => update({ backlogMax: e.target.value })} />
        </Field>
      </FieldRow>
    </>
  )
}

export function SubscriptionEditor({ existing, presetAgent, agents, sourceTypes, takenNames = [], onSave, onCancel }: {
  existing?: Subscription
  presetAgent?: string
  agents: AgentOption[]
  sourceTypes: string[]
  // Other subscriptions' names, refused before the server would.
  takenNames?: string[]
  onSave: (args: SubscriptionUpsert) => Promise<void>
  onCancel: () => void
}) {
  const [form, setForm] = useState<SubscriptionForm>(() => (existing ? formFor(existing) : newForm(presetAgent)))
  const { busy, error, setError, submit } = useSubmit()
  const timezone = useAuth().user?.timezone ?? 'UTC'
  // An edit makes a refused save's reason stale.
  const update = (change: Partial<SubscriptionForm>) => { setError(null); setForm(f => ({ ...f, ...change })) }
  const problem = formProblem(form, takenNames, wallClock(timezone))
  const problems = fieldProblems(form)
  // Every cron is incomplete while it is typed, so its problem waits until the field is left.
  const [cronLeft, setCronLeft] = useState(false)
  const nameTaken = form.source !== 'at' && takenNames.includes(form.name.trim())
  const [field, , fieldLabel] = errorField(error, form.source) ?? []
  const errorOn = (f: ErrorField) => (field === f && error ? userWording(error) : undefined)
  const problemId = useId()
  const takenId = useId()
  // A filter or webhook subscription keeps its handler kind; a schedule may move between agent and message.
  const handlerKinds = existing && existing.kind !== 'cron' ? [form.handler] : HANDLERS_FOR[form.source]
  const setSource = (source: SourceKind) =>
    update({ source, handler: HANDLERS_FOR[source].includes(form.handler) ? form.handler : HANDLERS_FOR[source][0] })
  const groups = [...new Set(agents.map(a => a.group))]

  return (
    <div className="space-y-4">
      {!existing && (
        <Fieldset legend="Source">
          <ToggleGroup label="Source" value={form.source} onChange={setSource}
            options={(Object.keys(SOURCE_LABELS) as SourceKind[]).map(k => ({ value: k, label: SOURCE_LABELS[k] }))} />
        </Fieldset>
      )}
      {existing && <p className="text-sm text-slate-600">Source: {SOURCE_LABELS[form.source]}</p>}
      {form.source !== 'at' && (
        <Field label="Name" hint={nameHint(form.name, !existing)}
          error={nameTaken ? problem ?? undefined : errorOn('name')}>
          <Input value={form.name} maxLength={MAX_NAME_CHARS} onChange={e => update({ name: e.target.value })} />
        </Field>
      )}
      <Fieldset legend="Hand to">
        <ToggleGroup label="Hand to" value={form.handler} onChange={(handler: HandlerKind) => update({ handler })}
          options={handlerKinds.map(k => ({ value: k, label: HANDLER_LABELS[k] }))} />
      </Fieldset>
      {form.handler === 'agent' && (
        <Field label="Agent">
          <Select value={form.agent} onChange={e => update({ agent: e.target.value })}>
            <option value="">Pick an agent</option>
            {groups.map(g => (
              <optgroup key={g} label={g}>
                {agents.filter(a => a.group === g).map(a => <option key={a.value} value={a.value}>{a.label}</option>)}
              </optgroup>
            ))}
          </Select>
        </Field>
      )}
      {form.handler === 'queue' && (
        <Field label="Queue" hint={existing?.kind === 'raw'
          ? "A webhook subscription's queue can't change."
          : form.source === 'raw'
            ? "Your workers claim its deliveries with agent_work_claim(queues=[...]). It can't be changed later."
            : 'Your workers claim its deliveries with agent_work_claim(queues=[...]).'}>
          <Input value={form.queue} disabled={existing?.kind === 'raw'} onChange={e => update({ queue: e.target.value })} />
        </Field>
      )}
      {form.handler === 'notify' && (
        <>
          <FieldRow>
            <Field label="Channel">
              <Select value={form.notify.channel} onChange={e => update({ notify: { ...form.notify, channel: e.target.value as NotifyChannel } })}>
                {NOTIFY_CHANNELS.map(c => <option key={c} value={c}>{capitalised(c)}</option>)}
              </Select>
            </Field>
            <Field label="To" hint="A person's name or email, a channel or user id, or an address.">
              <Input value={form.notify.target} onChange={e => update({ notify: { ...form.notify, target: e.target.value } })} />
            </Field>
          </FieldRow>
          {form.notify.channel === 'email' && (
            <Field label="Subject">
              <Input value={form.notify.subject ?? ''} onChange={e => update({ notify: { ...form.notify, subject: e.target.value } })} />
            </Field>
          )}
          <Field label="Message">
            <Textarea className="h-20" value={form.notify.message} onChange={e => update({ notify: { ...form.notify, message: e.target.value } })} />
          </Field>
        </>
      )}
      {form.source === 'cron' && (
        <Field label={`Cron (${timezone})`} hint={`For example, 0 9 * * * is every day at 09:00 ${timezone}.`} error={errorOn('cron') ?? (cronLeft ? problems.cron : undefined)}>
          <Input className="font-mono" value={form.cron} onChange={e => update({ cron: e.target.value })} onBlur={() => setCronLeft(true)} />
        </Field>
      )}
      {form.source === 'at' && (
        <Field label={`Fires at (${timezone})`}>
          <Input type="datetime-local" value={form.at} onChange={e => update({ at: e.target.value })} />
        </Field>
      )}
      {(form.source === 'cron' || form.source === 'at') && form.handler === 'agent' && (
        <Field label="Run input">
          <Textarea className="h-16" placeholder={form.source === 'at' ? 'What should the run do?' : 'What should each run do?'} value={form.input} onChange={e => update({ input: e.target.value })} />
        </Field>
      )}
      {form.source === 'filter' && <FilterFields form={form} update={update} sourceTypes={sourceTypes} regexError={errorOn('regex')} problems={problems} />}
      {form.source === 'filter' && <ItemTest form={form} existing={existing} />}
      {form.source === 'raw' && <RawFields form={form} update={update} locked={existing !== undefined} problems={problems} />}
      {/* By the buttons: a long form's top is off screen when a save is refused. */}
      {error && <Notice data-form-error tone="error">{fieldLabel ? `Not saved: see ${fieldLabel} above.` : userWording(error)}</Notice>}
      <FormActions>
        {/* Always rendered, so a screen reader hears each new reason. */}
        {/* A taken name is said on the Name field, so this copy isn't live: it says why Create is off when Name is scrolled away. */}
        <span id={problemId} aria-live="polite" className="text-xs text-slate-600">{nameTaken ? null : problem}</span>
        {nameTaken && <span id={takenId} className="text-xs text-slate-600">Name: {problem}</span>}
        <Button onClick={onCancel}>Cancel</Button>
        <Button variant="primary" disabled={problem !== null} aria-busy={busy} aria-describedby={problem ? (nameTaken ? takenId : problemId) : undefined}
          onClick={() => !busy && submit(() => onSave(toUpsert(form, existing)))}>
          {busy ? 'Saving...' : existing ? 'Save' : 'Create'}
        </Button>
      </FormActions>
    </div>
  )
}

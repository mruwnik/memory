import { useState } from 'react'
import { CHANGE_TAGS, NOTIFY_CHANNELS, type IngestFilter, type NotifyChannel, type Subscription, type SubscriptionUpsert } from '@/hooks/useAgents'
import { useAuth } from '@/hooks/useAuth'
import {
  Button, Checkbox, Field, FieldRow, Fieldset, FormActions, Input, MultiSelect, Notice, Select, Textarea, ToggleGroup, useSubmit,
} from '@/components/shared/ui'
import {
  formFor, formProblem, HANDLER_LABELS, HANDLERS_FOR, newForm, SOURCE_LABELS, toUpsert,
  type HandlerKind, type SourceKind, type SubscriptionForm,
} from './subscriptions'

export interface AgentOption { value: string; label: string; group: string }

const splitList = (text: string) => text.split(',').map(s => s.trim()).filter(Boolean)
const joined = (values: string[] | undefined) => (values ?? []).join(', ')

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

function FilterFields({ form, update, sourceTypes }: {
  form: SubscriptionForm
  update: (change: Partial<SubscriptionForm>) => void
  sourceTypes: string[]
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
        <Field label="Regex">
          <Input className="font-mono" value={config.filter.regex ?? ''} onChange={e => setFilter({ regex: e.target.value.trim() || null })} />
        </Field>
      </FieldRow>
      <Fieldset legend="Only" className="flex flex-col">
        {FLAGS.map(([field, label]) => (
          <Checkbox key={field} label={label} checked={config.filter[field]} onChange={e => setFilter({ [field]: e.target.checked })} />
        ))}
        <Checkbox label="Skip my own Slack messages" checked={config.filter.exclude_own ?? ownDefault}
          onChange={e => setFilter({ exclude_own: e.target.checked })} />
      </Fieldset>
      <Fieldset legend="Delivery" className="flex flex-col space-y-2">
        <Checkbox label="Batch items together" checked={config.delivery.mode === 'batch'}
          onChange={e => update({ config: { ...config, delivery: e.target.checked ? { mode: 'batch', window_sec: 3600, max_items: 50 } : { mode: 'per_item' } } })} />
        {batch && (
          <FieldRow>
            <Field label="Window (seconds)">
              <Input type="number" min="60" max="86400" value={batch.window_sec}
                onChange={e => update({ config: { ...config, delivery: { ...batch, window_sec: Number(e.target.value) } } })} />
            </Field>
            <Field label="Max items">
              <Input type="number" min="1" max="200" value={batch.max_items}
                onChange={e => update({ config: { ...config, delivery: { ...batch, max_items: Number(e.target.value) } } })} />
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
        <Field label="Deliveries per hour" hint="Beyond it they wait an hour, then are dropped.">
          <Input type="number" min="1" placeholder="Server default" value={form.maxPerHour} onChange={e => update({ maxPerHour: e.target.value })} />
        </Field>
        <Field label="Most waiting" hint="Beyond it the oldest are dropped.">
          <Input type="number" min="1" placeholder="No limit" value={form.backlogMax} onChange={e => update({ backlogMax: e.target.value })} />
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
  const { busy, error, submit } = useSubmit()
  const timezone = useAuth().user?.timezone ?? 'UTC'
  const update = (change: Partial<SubscriptionForm>) => setForm(f => ({ ...f, ...change }))
  const problem = formProblem(form, takenNames)
  // A filter subscription keeps its handler kind; a schedule may move between agent and message.
  const handlerKinds = existing?.kind === 'ingest' ? [form.handler] : HANDLERS_FOR[form.source]
  const setSource = (source: SourceKind) =>
    update({ source, handler: HANDLERS_FOR[source].includes(form.handler) ? form.handler : HANDLERS_FOR[source][0] })
  const groups = [...new Set(agents.map(a => a.group))]

  return (
    <div className="space-y-4">
      {error && <Notice tone="error">{error}</Notice>}
      {!existing && (
        <Fieldset legend="Source">
          <ToggleGroup label="Source" value={form.source} onChange={setSource}
            options={(Object.keys(SOURCE_LABELS) as SourceKind[]).map(k => ({ value: k, label: SOURCE_LABELS[k] }))} />
        </Fieldset>
      )}
      {form.source !== 'at' && (
        <Field label="Name" hint={existing ? undefined : 'Leave empty for a numbered name.'}>
          <Input value={form.name} onChange={e => update({ name: e.target.value })} />
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
        <Field label="Queue" hint="Your workers claim its deliveries with agent_work_claim(queue=...).">
          <Input value={form.queue} onChange={e => update({ queue: e.target.value })} />
        </Field>
      )}
      {form.handler === 'notify' && (
        <>
          <FieldRow>
            <Field label="Channel">
              <Select value={form.notify.channel} onChange={e => update({ notify: { ...form.notify, channel: e.target.value as NotifyChannel } })}>
                {NOTIFY_CHANNELS.map(c => <option key={c} value={c}>{c}</option>)}
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
        <Field label={`Cron (${timezone})`} hint={`For example, 0 9 * * * is every day at 09:00 ${timezone}.`}>
          <Input className="font-mono" value={form.cron} onChange={e => update({ cron: e.target.value })} />
        </Field>
      )}
      {form.source === 'at' && (
        <Field label={`Fires at (${timezone})`}>
          <Input type="datetime-local" value={form.at} onChange={e => update({ at: e.target.value })} />
        </Field>
      )}
      {form.source !== 'filter' && form.handler === 'agent' && (
        <Field label="Run input">
          <Textarea className="h-16" placeholder="What should each run do?" value={form.input} onChange={e => update({ input: e.target.value })} />
        </Field>
      )}
      {form.source === 'filter' && <FilterFields form={form} update={update} sourceTypes={sourceTypes} />}
      <FormActions>
        {problem && <span className="text-xs text-slate-600">{problem}</span>}
        <Button onClick={onCancel}>Cancel</Button>
        <Button variant="primary" disabled={busy || problem !== null} onClick={() => submit(() => onSave(toUpsert(form, existing)))}>
          {busy ? 'Saving...' : existing ? 'Save' : 'Create'}
        </Button>
      </FormActions>
    </div>
  )
}

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  DEFAULT_KEY_DAILY_CAP_USD, KEY_PROVIDERS, PROVIDER_NAMES, useAgents,
  type KeyDeleteResult, type KeyProvider, type KeyUpsert, type ProviderKey,
} from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { formatCost } from '@/utils/format'
import { Badge, Button, ConfirmDialog, EmptyState, Field, FieldRow, FormActions, Input, List, Modal, Notice, Page, Row, RowError, Section, Select, useSubmit } from '@/components/shared/ui'

type Keys = Record<KeyProvider, ProviderKey[]>
type Save = (body: KeyUpsert) => Promise<void>

const CAP_HINT = `Optional. Without one, ${formatCost(DEFAULT_KEY_DAILY_CAP_USD)}/day applies unless the agent sets a daily cap.`

function AddKeyForm({ initialProvider, onSave }: { initialProvider: KeyProvider; onSave: Save }) {
  const [provider, setProvider] = useState<KeyProvider>(initialProvider)
  const [label, setLabel] = useState('')
  const [key, setKey] = useState('')
  const [cap, setCap] = useState('')
  const { busy, error, submit } = useSubmit()

  const add = (e: FormEvent) => {
    e.preventDefault()
    submit(() => onSave({ provider, label: label.trim(), key, ...(cap.trim() ? { maxCostUsdPerDay: cap.trim() } : {}) }))
  }

  return (
    <form className="space-y-4" onSubmit={add}>
      <FieldRow>
        <Field label="Provider">
          <Select value={provider} onChange={e => setProvider(e.target.value as KeyProvider)}>
            {KEY_PROVIDERS.map(p => <option key={p} value={p}>{PROVIDER_NAMES[p]}</option>)}
          </Select>
        </Field>
        <Field label="Label">
          <Input required maxLength={100} placeholder="personal" value={label} onChange={e => setLabel(e.target.value)} />
        </Field>
        <Field label="API key" hint={`Checked with ${PROVIDER_NAMES[provider]} before it is saved, and never shown again.`}>
          <Input type="password" required autoComplete="off" value={key} onChange={e => setKey(e.target.value)} />
        </Field>
        <Field label="Max $ per day" hint={CAP_HINT}>
          <Input inputMode="decimal" placeholder={DEFAULT_KEY_DAILY_CAP_USD} value={cap} onChange={e => setCap(e.target.value)} />
        </Field>
      </FieldRow>
      {error && <Notice tone="error">{error}</Notice>}
      <FormActions>
        <Button type="submit" variant="primary" disabled={busy || !label.trim() || !key}>
          {busy ? `Checking with ${PROVIDER_NAMES[provider]}...` : 'Add key'}
        </Button>
      </FormActions>
    </form>
  )
}

type Edit = 'label' | 'key' | 'cap'

const EDITS: Record<Edit, { label: string; initial: (k: ProviderKey) => string; body: (value: string) => KeyUpsert }> = {
  label: { label: 'Label', initial: k => k.label, body: value => ({ label: value.trim() }) },
  key: { label: 'New API key', initial: () => '', body: value => ({ key: value }) },
  cap: { label: 'Max $ per day', initial: k => k.max_cost_usd_per_day ?? '', body: value => ({ maxCostUsdPerDay: value.trim() }) },
}

function EditKeyForm({ entry, edit, onSave, onDone }: { entry: ProviderKey; edit: Edit; onSave: Save; onDone: () => void }) {
  const [value, setValue] = useState(EDITS[edit].initial(entry))
  const { busy, error, submit } = useSubmit()
  const save = (e: FormEvent) => {
    e.preventDefault()
    submit(async () => {
      await onSave({ keyId: entry.id, ...EDITS[edit].body(value) })
      onDone()
    })
  }

  return (
    <form className="w-full flex flex-wrap items-start gap-2" onSubmit={save}>
      <Field className="flex-1 min-w-48" label={EDITS[edit].label} error={error}
        hint={edit === 'cap' ? `Leave empty for no cap of its own (${formatCost(DEFAULT_KEY_DAILY_CAP_USD)}/day unless the agent sets one).` : undefined}>
        {edit === 'key'
          ? <Input autoFocus type="password" required autoComplete="off" value={value} onChange={e => setValue(e.target.value)} />
          : edit === 'label'
            ? <Input autoFocus required maxLength={100} value={value} onChange={e => setValue(e.target.value)} />
            : <Input autoFocus inputMode="decimal" placeholder={DEFAULT_KEY_DAILY_CAP_USD} value={value} onChange={e => setValue(e.target.value)} />}
      </Field>
      <div className="flex gap-2 pt-6">
        <Button type="submit" size="sm" variant="primary" disabled={busy || (edit !== 'cap' && !value.trim())}>
          {busy && edit === 'key' ? `Checking with ${PROVIDER_NAMES[entry.provider]}...` : 'Save'}
        </Button>
        <Button type="button" size="sm" onClick={onDone}>Cancel</Button>
      </div>
    </form>
  )
}

function KeyRow({ entry, onSave, onDelete }: {
  entry: ProviderKey
  onSave: Save
  onDelete: () => void
}) {
  const [edit, setEdit] = useState<Edit | null>(null)
  const makeDefault = useSubmit()
  // Opening an edit hides the row's buttons, so closing it puts focus back on the one that opened it.
  const openers = useRef<Partial<Record<Edit, HTMLButtonElement | null>>>({})
  const closed = useRef<Edit | null>(null)
  useEffect(() => {
    if (edit !== null || closed.current === null) return
    openers.current[closed.current]?.focus()
    closed.current = null
  }, [edit])
  const done = () => {
    closed.current = edit
    setEdit(null)
  }
  const cap = entry.max_cost_usd_per_day ? `${formatCost(entry.max_cost_usd_per_day)}/day cap` : `${formatCost(DEFAULT_KEY_DAILY_CAP_USD)}/day default cap`

  return (
    <Row
      title={entry.label}
      badges={<>
        <span className="font-mono text-xs text-slate-600">••••{entry.key_hint}</span>
        {entry.is_default && <Badge tone="primary">Default</Badge>}
        {entry.status === 'ok' ? <Badge tone="success">OK</Badge> : <Badge tone="danger">Rejected</Badge>}
      </>}
      meta={[`#${entry.id}`, entry.last_used_at ? `Used ${formatRelativeTime(entry.last_used_at)}` : 'Never used', cap]}
      actions={edit === null && <>
        <Button size="sm" ref={el => { openers.current.label = el }} onClick={() => setEdit('label')}>Rename</Button>
        <Button size="sm" ref={el => { openers.current.key = el }} onClick={() => setEdit('key')}>Replace key</Button>
        <Button size="sm" ref={el => { openers.current.cap = el }} onClick={() => setEdit('cap')}>Daily cap</Button>
        {!entry.is_default && (
          <Button size="sm" disabled={makeDefault.busy}
            onClick={() => makeDefault.submit(() => onSave({ keyId: entry.id, isDefault: true }))}>
            {makeDefault.busy ? 'Saving...' : 'Make default'}
          </Button>
        )}
        <Button size="sm" variant="danger" onClick={onDelete}>Delete</Button>
      </>}
    >
      {entry.status === 'invalid' && (
        <RowError>
          {entry.status_detail ?? `${PROVIDER_NAMES[entry.provider]} rejected this key.`} LLM work that uses it fails until you replace it.
        </RowError>
      )}
      {makeDefault.error && <RowError>{makeDefault.error}</RowError>}
      {edit && <EditKeyForm entry={entry} edit={edit} onSave={onSave} onDone={done} />}
    </Row>
  )
}

/** What deleting a key changed, from the agent_key_delete response. */
const deleteOutcome = (entry: ProviderKey, result: KeyDeleteResult, keys: Keys) => {
  const successor = keys[entry.provider].find(k => k.id === result.new_default)
  return [
    `Deleted '${entry.label}'.`,
    result.pinned_by.length > 0 && `These agents used it and fail until you pick another key: ${result.pinned_by.join(', ')}.`,
    successor && `'${successor.label}' is now your default ${PROVIDER_NAMES[entry.provider]} key.`,
  ].filter(Boolean).join('\n')
}

export default function KeysPage() {
  const { listKeys, upsertKey, deleteKey } = useAgents()
  const [keys, setKeys] = useState<Keys | null>(null)
  const [adding, setAdding] = useState<KeyProvider | null>(null)
  const [deleting, setDeleting] = useState<ProviderKey | null>(null)
  const [outcome, setOutcome] = useState<{ tone: 'success' | 'warning'; message: string } | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoadError(null)
    listKeys()
      .then(result => setKeys(result.providers))
      .catch(e => setLoadError(e.message))
  }, [listKeys])
  useEffect(load, [load])

  const save: Save = async body => {
    await upsertKey(body)
    setOutcome(null)
    load()
  }
  const act = (work: () => Promise<void>) => {
    setActionError(null)
    work().catch(e => setActionError(e.message))
  }
  const confirmDelete = (entry: ProviderKey) => act(async () => {
    setDeleting(null)
    const result = await deleteKey(entry.id)
    setOutcome({ tone: result.pinned_by.length ? 'warning' : 'success', message: deleteOutcome(entry, result, keys!) })
    load()
  })

  return (
    <Page
      title="LLM keys"
      description="Anthropic keys pay for summaries and other LLM work on items you own, and for the LLM steps of your searches. Anthropic and OpenAI keys both pay for agents on their models. Nothing LLM-backed runs without one. Tools refer to keys by name or id."
      actions={<Button variant="primary" onClick={() => setAdding('anthropic')}>New key</Button>}
      loading={keys === null && 'Loading keys...'}
      error={loadError}
      onRetry={load}
    >
      {adding && (
        <Modal title="Add LLM key" onClose={() => setAdding(null)} size="xl">
          <AddKeyForm initialProvider={adding} onSave={async body => {
            await save(body)
            setAdding(null)
          }} />
        </Modal>
      )}
      {outcome && <Notice tone={outcome.tone} onDismiss={() => setOutcome(null)}>{outcome.message}</Notice>}
      {actionError && <Notice tone="error" onDismiss={() => setActionError(null)}>{actionError}</Notice>}
      {keys && KEY_PROVIDERS.map(provider => (
        <Section key={provider} title={PROVIDER_NAMES[provider]}>
          {keys[provider].length === 0
            ? <EmptyState message={`No ${PROVIDER_NAMES[provider]} keys`}
                actionLabel={`Add ${PROVIDER_NAMES[provider]} key`} onAction={() => setAdding(provider)} />
            : (
              <List>
                {keys[provider].map(entry => (
                  <KeyRow key={entry.id} entry={entry} onSave={save} onDelete={() => setDeleting(entry)} />
                ))}
              </List>
            )}
        </Section>
      ))}
      {deleting && (
        <ConfirmDialog title="Delete key" confirmLabel="Delete"
          message={`Delete the ${PROVIDER_NAMES[deleting.provider]} key '${deleting.label}'? Agents that pin it stop working until you pick another key, and sources that use it go back to their default key.`}
          onConfirm={() => confirmDelete(deleting)} onCancel={() => setDeleting(null)} />
      )}
    </Page>
  )
}

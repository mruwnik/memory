import { useCallback, useEffect, useState } from 'react'
import { useUsers, type ApiKey, type User } from '../../hooks/useUsers'
import {
  Badge, Button, ConfirmButton, EmptyState, Field, FormActions, Input, LoadingState, Modal,
  MultiSelect, Notice, Section, Select, Table, useActionNotice, type MultiSelectOption,
} from '@/components/shared/ui'
import { NewApiKey } from './NewApiKey'

const formatDate = (iso: string | null, empty: string) => (iso ? new Date(iso).toLocaleDateString() : empty)

const isExpired = (key: ApiKey) => !!key.expires_at && new Date(key.expires_at) < new Date()

const scopeLabel = (scope: string) => (scope === '*' ? 'All permissions' : scope)

const NewKeyForm = ({ user, onCreated, onCancel }: { user: User; onCreated: (key: string) => void; onCancel: () => void }) => {
  const { createApiKey, listApiKeyTypes, listScopes } = useUsers()
  const [types, setTypes] = useState<string[]>(['internal'])
  // Users with * can grant any scope; everyone else only the ones they hold.
  const [scopeOptions, setScopeOptions] = useState<(string | MultiSelectOption)[]>(user.scopes)
  const [name, setName] = useState('')
  const [keyType, setKeyType] = useState('internal')
  const [scopes, setScopes] = useState<string[]>([])
  const [expiresInDays, setExpiresInDays] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listApiKeyTypes().then(setTypes, () => {})
    if (user.scopes.includes('*')) {
      listScopes().then((all) => setScopeOptions(all.map((s) => ({ value: s.value, label: s.label, description: s.description }))), () => {})
    }
  }, [listApiKeyTypes, listScopes, user.scopes])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const created = await createApiKey(user.id, {
        name: name || undefined,
        key_type: keyType,
        scopes: scopes.length ? scopes : null,
        expires_in_days: expiresInDays ? Number(expiresInDays) : null,
      })
      onCreated(created.key)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create API key')
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      {error && <Notice tone="error">{error}</Notice>}
      <Field label="Name" hint="What uses this key, so you can tell keys apart later.">
        <Input value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Type" hint="Some integrations only accept keys of their own type.">
        <Select value={keyType} onChange={(e) => setKeyType(e.target.value)}>
          {types.map((t) => <option key={t} value={t}>{t}</option>)}
        </Select>
      </Field>
      <Field label="Scopes" hint="Leave empty to give the key all your scopes, including ones you gain later.">
        <MultiSelect options={scopeOptions} value={scopes} onChange={setScopes} placeholder="All your scopes" />
      </Field>
      <Field label="Expires after (days)" hint="Leave empty for a key that doesn't expire.">
        <Input type="number" min={1} value={expiresInDays} onChange={(e) => setExpiresInDays(e.target.value)} />
      </Field>
      <FormActions>
        <Button onClick={onCancel}>Cancel</Button>
        <Button variant="primary" type="submit" disabled={saving}>{saving ? 'Creating...' : 'Create key'}</Button>
      </FormActions>
    </form>
  )
}

/** The user's live API keys, with create and revoke. */
export const ApiKeys = ({ user }: { user: User }) => {
  const { listApiKeys, revokeApiKey } = useUsers()
  const { run, notice } = useActionNotice()
  const [keys, setKeys] = useState<ApiKey[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [newKey, setNewKey] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoadError(null)
    try {
      setKeys((await listApiKeys(user.id)).filter((k) => !k.revoked))
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : 'Failed to fetch API keys')
    }
  }, [listApiKeys, user.id])

  useEffect(() => { load() }, [load])

  const revoke = (id: number) => run(async () => {
    await revokeApiKey(user.id, id)
    await load()
  }, 'API key revoked')

  const closeModal = () => {
    setCreating(false)
    setNewKey(null)
  }

  const body = () => {
    if (loadError) return <Notice tone="error" onRetry={load}>{loadError}</Notice>
    if (!keys) return <LoadingState message="Loading API keys..." />
    if (!keys.length) return <EmptyState message="No API keys yet" />
    return (
      <Table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>Scopes</th>
            <th>Key</th>
            <th>Created</th>
            <th>Last used</th>
            <th>Expires</th>
            <th><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {keys.map((key) => (
            <tr key={key.id}>
              <td className="font-medium text-slate-900">{key.name || 'Unnamed'}</td>
              <td><Badge>{key.key_type}</Badge></td>
              <td>
                {key.scopes === null
                  ? <span className="text-slate-600 whitespace-nowrap">All your scopes</span>
                  : <div className="flex flex-wrap gap-1">{key.scopes.map((s) => <Badge key={s}>{scopeLabel(s)}</Badge>)}</div>}
              </td>
              <td><code className="text-xs text-slate-700 whitespace-nowrap">{key.key_preview}</code></td>
              <td className="whitespace-nowrap text-slate-600">{formatDate(key.created_at, '')}</td>
              <td className="whitespace-nowrap text-slate-600">{formatDate(key.last_used_at, 'Never')}</td>
              <td className="whitespace-nowrap text-slate-600">
                {isExpired(key) ? <Badge tone="warning">Expired</Badge> : formatDate(key.expires_at, 'No expiry')}
              </td>
              <td>
                <ConfirmButton confirm={`Revoke ${key.name || 'this key'}? Anything using it will stop working.`}
                  onConfirm={() => revoke(key.id)}>Revoke</ConfirmButton>
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
    )
  }

  return (
    <Section title="API keys" description="Keys let scripts, integrations and MCP clients act as you."
      actions={<Button variant="primary" size="sm" onClick={() => setCreating(true)}>New API key</Button>}>
      {notice}
      {body()}
      {creating && (
        <Modal size="md" title={newKey ? 'API key created' : 'New API key'} onClose={newKey ? undefined : closeModal}>
          {newKey
            ? <NewApiKey apiKey={newKey} onDone={() => { closeModal(); load() }} />
            : <NewKeyForm user={user} onCreated={setNewKey} onCancel={closeModal} />}
        </Modal>
      )}
    </Section>
  )
}

import { useCallback, useEffect, useState } from 'react'
import { useSSHKeys, type SSHKey } from '@/hooks/useSSHKeys'
import { useAuth } from '@/hooks/useAuth'
import { Button, Card, ConfirmButton, CopyButton, EmptyState, ErrorState, Field, FormActions, Input, LoadingState, Modal, Section, Select, Textarea, useActionNotice } from '@/components/shared/ui'

export const SSHKeysPanel = () => {
  const { list, save, remove, setDefault } = useSSHKeys()
  const { hasScope } = useAuth()
  const [keys, setKeys] = useState<SSHKey[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<SSHKey | 'new' | null>(null)
  const { run, notice } = useActionNotice()
  const load = useCallback(async () => {
    try { setKeys(await list()); setError(null) }
    catch (e) { setError(e instanceof Error ? e.message : 'Unable to load SSH keys') }
  }, [list])
  useEffect(() => { void load() }, [load])
  if (error) return <ErrorState message={error} onRetry={load} />
  if (!keys) return <LoadingState />
  return <Section description="Import or generate SSH keys for Git access. Personal keys belong to you; instance keys are managed by admins. Notes and containers use these database keys.">
    {notice}
    <Button onClick={() => setEditing('new')}>Add SSH key</Button>
    {!keys.length && <EmptyState message="No SSH keys configured" />}
    {keys.map(key => <Card key={key.id} title={key.name} description={`${key.user_id === null ? 'Instance' : 'Personal'}${key.is_default ? ' · Your container default' : ''}`}>
      <p className="break-all font-mono text-sm">{key.fingerprint ?? 'Fingerprint unavailable for migrated key'}</p>
      <Field label="Public key"><div className="flex items-start gap-2"><code className="break-all text-xs">{key.public_key}</code><CopyButton text={key.public_key} /></div></Field>
      <FormActions>
        <Button onClick={() => setEditing(key)}>Edit or replace</Button>
        <Button onClick={() => run(async () => { await setDefault(key.is_default ? null : key.id); await load() })}>{key.is_default ? 'Clear container default' : 'Use for my containers'}</Button>
        <ConfirmButton confirm="Delete this SSH key? Running containers retain keys already injected; future sessions will no longer use it." onConfirm={() => run(async () => { await remove(key.id); await load() })}>Delete</ConfirmButton>
      </FormActions>
    </Card>)}
    {editing && <KeyForm existing={editing === 'new' ? undefined : editing} admin={hasScope('*') || hasScope('admin')}
      onClose={() => setEditing(null)} onSave={async (input) => { await save(input, editing === 'new' ? undefined : editing.id); setEditing(null); await load() }} />}
  </Section>
}

function KeyForm({ existing, admin, onClose, onSave }: { existing?: SSHKey; admin: boolean; onClose: () => void; onSave: (input: import('@/hooks/useSSHKeys').SSHKeyInput) => Promise<void> }) {
  const [name, setName] = useState(existing?.name ?? '')
  const [privateKey, setPrivateKey] = useState('')
  const [passphrase, setPassphrase] = useState('')
  const [method, setMethod] = useState('import')
  const [ownership, setOwnership] = useState('personal')
  const [busy, setBusy] = useState(false)
  const { run, notice } = useActionNotice()
  const submit = async () => {
    setBusy(true)
    await run(() => onSave({ name, ...(privateKey ? { private_key: privateKey, passphrase } : {}), ...(!existing ? { generate: method === 'generate', instance: ownership === 'instance' } : {}) }))
    setBusy(false)
  }
  return <Modal title={existing ? 'Edit SSH key' : 'Add SSH key'} onClose={onClose}>
    <form className="space-y-4" onSubmit={e => { e.preventDefault(); void submit() }}>
      {notice}
      <Field label="Name"><Input required value={name} onChange={e => setName(e.target.value)} maxLength={200} /></Field>
      {!existing && <>
        <Field label="Method"><Select value={method} onChange={e => { setMethod(e.target.value); setPrivateKey(''); setPassphrase('') }}><option value="import">Provide a private key</option><option value="generate">Generate an Ed25519 key</option></Select></Field>
        {admin && <Field label="Ownership" hint="Personal keys belong to you. Instance keys are shared with other admins."><Select value={ownership} onChange={e => setOwnership(e.target.value)}><option value="personal">Personal</option><option value="instance">Instance</option></Select></Field>}
      </>}
      {method === 'import' && <>
        <Field label="Private key" hint={existing ? 'Leave blank to keep the existing key. Replacing it affects future connections.' : 'Paste or upload an OpenSSH or PEM private key. Stored encrypted; never displayed again.'}>
          <Textarea required={!existing} value={privateKey} onChange={e => setPrivateKey(e.target.value)} rows={6} spellCheck={false} autoComplete="off" />
        </Field>
        <Field label="Upload private key"><Input type="file" onChange={e => { const file = e.target.files?.[0]; if (file) void run(async () => { if (file.size > 65536) throw new Error('Key file is too large'); setPrivateKey(await file.text()) }) }} /></Field>
        <Field label="Passphrase" hint="Only used to unlock the imported key. Memory encrypts the unlocked key for unattended use."><Input type="password" autoComplete="new-password" value={passphrase} onChange={e => setPassphrase(e.target.value)} /></Field>
      </>}
      <FormActions><Button onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" disabled={busy}>{busy ? 'Saving...' : 'Save key'}</Button></FormActions>
    </form>
  </Modal>
}

import { useState, useEffect, useCallback } from 'react'
import { useSecrets, Secret, SecretWithValue } from '@/hooks/useSecrets'
import { Button, EmptyState, ErrorState, Field, FormActions, Input, LoadingState, Modal, Notice, Textarea, ConfirmButton, List, Row, Section, useActionNotice } from '@/components/shared/ui'

export const SecretsPanel = () => {
  const { listSecrets, createSecret, updateSecret, deleteSecret, getSecretValue } = useSecrets()
  const [secrets, setSecrets] = useState<Secret[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [editingSecret, setEditingSecret] = useState<Secret | null>(null)
  const [revealedSecrets, setRevealedSecrets] = useState<Record<number, string>>({})
  const [loadingReveal, setLoadingReveal] = useState<Record<number, boolean>>({})

  const loadSecrets = useCallback(async () => {
    try {
      const data = await listSecrets()
      setSecrets(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load secrets')
    } finally {
      setLoading(false)
    }
  }, [listSecrets])

  useEffect(() => { loadSecrets() }, [loadSecrets])

  const { run, notice } = useActionNotice()

  const forget = (id: number) => setRevealedSecrets(prev => {
    const next = { ...prev }
    delete next[id]
    return next
  })

  // Form handlers throw, so the open modal shows the failure.
  const handleCreate = async (data: { name: string; value: string; description?: string }) => {
    await createSecret(data)
    setShowForm(false)
    loadSecrets()
  }

  const handleUpdate = async (data: { value?: string; description?: string }) => {
    if (!editingSecret) return
    await updateSecret(editingSecret.id, data)
    setEditingSecret(null)
    if (data.value) forget(editingSecret.id)
    loadSecrets()
  }

  const handleDelete = (id: number) => run(async () => {
    await deleteSecret(id)
    forget(id)
    loadSecrets()
  })

  const handleToggleReveal = async (secret: Secret) => {
    if (revealedSecrets[secret.id]) return forget(secret.id)
    setLoadingReveal(prev => ({ ...prev, [secret.id]: true }))
    await run(async () => {
      const data = await getSecretValue(secret.id)
      setRevealedSecrets(prev => ({ ...prev, [secret.id]: data.value }))
    })
    setLoadingReveal(prev => ({ ...prev, [secret.id]: false }))
  }

  const handleCopy = (secret: Secret) => run(async () => {
    const value = revealedSecrets[secret.id] ?? (await getSecretValue(secret.id)).value
    await navigator.clipboard.writeText(value)
  }, `Copied ${secret.name}`)

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadSecrets} />

  return (
    <Section
      description="Encrypted storage for API keys, tokens, and other sensitive values"
      actions={<Button variant="primary" onClick={() => setShowForm(true)}>Add secret</Button>}
    >
      {notice}
      {secrets.length === 0 ? (
        <EmptyState message="No secrets configured" actionLabel="Add secret" onAction={() => setShowForm(true)} />
      ) : (
        <List>
          {secrets.map(secret => (
            <SecretRow
              key={secret.id}
              secret={secret}
              revealedValue={revealedSecrets[secret.id]}
              isRevealing={loadingReveal[secret.id]}
              onToggleReveal={() => handleToggleReveal(secret)}
              onCopy={() => handleCopy(secret)}
              onEdit={() => setEditingSecret(secret)}
              onDelete={() => handleDelete(secret.id)}
            />
          ))}
        </List>
      )}

      {showForm && <SecretForm onSubmit={handleCreate} onCancel={() => setShowForm(false)} />}
      {editingSecret && <SecretForm secret={editingSecret} onSubmit={handleUpdate} onCancel={() => setEditingSecret(null)} />}
    </Section>
  )
}

interface SecretRowProps {
  secret: Secret
  revealedValue?: string
  isRevealing?: boolean
  onToggleReveal: () => void
  onCopy: () => void
  onEdit: () => void
  onDelete: () => void
}

const SecretRow = ({ secret, revealedValue, isRevealing, onToggleReveal, onCopy, onEdit, onDelete }: SecretRowProps) => (
  <Row
    title={<span className="font-mono">{secret.name}</span>}
    meta={[secret.description, `Updated ${new Date(secret.updated_at).toLocaleDateString()}`]}
    actions={<>
      <Button size="sm" onClick={onEdit} title="Edit secret">Edit</Button>
      <ConfirmButton confirm={`Delete the secret "${secret.name}"? This action cannot be undone.`} onConfirm={onDelete} title="Delete secret" />
    </>}
  >
    <div className="flex items-center gap-2">
      <div className="flex-1 min-w-0 font-mono text-sm bg-slate-100 rounded px-2 py-1">
        {revealedValue
          ? <span className="text-slate-800 break-all">{revealedValue}</span>
          : <span className="text-slate-500"><span aria-hidden="true">{'*'.repeat(24)}</span><span className="sr-only">Hidden value</span></span>}
      </div>
      <Button variant="ghost" size="icon" onClick={onToggleReveal} disabled={isRevealing}
        title={revealedValue ? 'Hide value' : 'Reveal value'} aria-label={revealedValue ? 'Hide value' : 'Reveal value'}>
        {isRevealing ? <LoadingIcon /> : revealedValue ? <EyeOffIcon /> : <EyeIcon />}
      </Button>
      <Button variant="ghost" size="icon" onClick={onCopy} title="Copy to clipboard" aria-label="Copy to clipboard">
        <CopyIcon />
      </Button>
    </div>
  </Row>
)

// Simple inline icons
const EyeIcon = () => (
  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
  </svg>
)

const EyeOffIcon = () => (
  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.875 18.825A10.05 10.05 0 0112 19c-4.478 0-8.268-2.943-9.543-7a9.97 9.97 0 011.563-3.029m5.858.908a3 3 0 114.243 4.243M9.878 9.878l4.242 4.242M9.88 9.88l-3.29-3.29m7.532 7.532l3.29 3.29M3 3l3.59 3.59m0 0A9.953 9.953 0 0112 5c4.478 0 8.268 2.943 9.543 7a10.025 10.025 0 01-4.132 5.411m0 0L21 21" />
  </svg>
)

const CopyIcon = () => (
  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
  </svg>
)

const LoadingIcon = () => (
  <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
  </svg>
)

interface SecretFormProps {
  secret?: Secret
  onSubmit: (data: { name?: string; value?: string; description?: string }) => Promise<void>
  onCancel: () => void
}

const SecretForm = ({ secret, onSubmit, onCancel }: SecretFormProps) => {
  const [formData, setFormData] = useState({
    name: secret?.name || '',
    value: '',
    description: secret?.description || '',
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      if (secret) {
        // Update - only send changed fields
        const updates: { value?: string; description?: string } = {}
        if (formData.value) updates.value = formData.value
        if (formData.description !== secret.description) updates.description = formData.description
        await onSubmit(updates)
      } else {
        // Create - send all fields
        await onSubmit(formData)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title={secret ? 'Edit secret' : 'Add secret'} onClose={onCancel}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}

        <Field label="Name" hint="Must be a valid identifier (letters, digits, and -_*+!?'<>=)">
          <Input
            type="text"
            value={formData.name}
            onChange={e => setFormData({ ...formData, name: e.target.value })}
            required={!secret}
            disabled={!!secret}
            placeholder="api-key or github-token"
            className="font-mono"
          />
        </Field>

        <Field label={secret ? 'New value (leave empty to keep current)' : 'Value'}>
          <Input
            type="password"
            value={formData.value}
            onChange={e => setFormData({ ...formData, value: e.target.value })}
            required={!secret}
            placeholder={secret ? 'Enter new value to update' : 'Enter secret value'}
            className="font-mono"
            autoComplete="off"
          />
        </Field>

        <Field label="Description (optional)">
          <Textarea
            value={formData.description}
            onChange={e => setFormData({ ...formData, description: e.target.value })}
            rows={2}
            placeholder="What is this secret used for?"
          />
        </Field>

        <FormActions>
          <Button type="button" onClick={onCancel}>Cancel</Button>
          <Button variant="primary" type="submit" disabled={submitting}>
            {submitting ? 'Saving...' : 'Save'}
          </Button>
        </FormActions>
      </form>
    </Modal>
  )
}

export default SecretsPanel

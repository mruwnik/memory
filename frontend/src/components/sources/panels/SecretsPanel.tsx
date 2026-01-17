import { useState, useEffect, useCallback } from 'react'
import { useSecrets, Secret, SecretWithValue } from '@/hooks/useSecrets'
import {
  Modal,
  EmptyState,
  LoadingState,
  ErrorState,
} from '../shared'
import { styles } from '../styles'

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
    setLoading(true)
    setError(null)
    try {
      const data = await listSecrets()
      setSecrets(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load secrets')
    } finally {
      setLoading(false)
    }
  }, [listSecrets])

  useEffect(() => { loadSecrets() }, [loadSecrets])

  const handleCreate = async (data: { name: string; value: string; description?: string }) => {
    try {
      await createSecret(data)
      setShowForm(false)
      loadSecrets()
    } catch (e) {
      throw e // Re-throw so the form can display the error
    }
  }

  const handleUpdate = async (data: { value?: string; description?: string }) => {
    if (editingSecret) {
      try {
        await updateSecret(editingSecret.id, data)
        setEditingSecret(null)
        // Clear revealed value if it was updated
        if (data.value) {
          setRevealedSecrets(prev => {
            const next = { ...prev }
            delete next[editingSecret.id]
            return next
          })
        }
        loadSecrets()
      } catch (e) {
        throw e
      }
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Are you sure you want to delete this secret? This action cannot be undone.')) {
      return
    }
    try {
      await deleteSecret(id)
      setRevealedSecrets(prev => {
        const next = { ...prev }
        delete next[id]
        return next
      })
      loadSecrets()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete secret')
    }
  }

  const handleToggleReveal = async (secret: Secret) => {
    if (revealedSecrets[secret.id]) {
      // Hide the secret
      setRevealedSecrets(prev => {
        const next = { ...prev }
        delete next[secret.id]
        return next
      })
    } else {
      // Reveal the secret
      setLoadingReveal(prev => ({ ...prev, [secret.id]: true }))
      try {
        const data = await getSecretValue(secret.id)
        setRevealedSecrets(prev => ({ ...prev, [secret.id]: data.value }))
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to reveal secret')
      } finally {
        setLoadingReveal(prev => ({ ...prev, [secret.id]: false }))
      }
    }
  }

  const handleCopy = async (secret: Secret) => {
    const value = revealedSecrets[secret.id]
    if (value) {
      await navigator.clipboard.writeText(value)
    } else {
      // Fetch and copy without revealing
      try {
        const data = await getSecretValue(secret.id)
        await navigator.clipboard.writeText(data.value)
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to copy secret')
      }
    }
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadSecrets} />

  return (
    <div className={styles.panel}>
      <div className={styles.panelHeader}>
        <div>
          <h3 className={styles.panelTitle}>Secrets</h3>
          <p className="text-sm text-slate-500 mt-1">
            Encrypted storage for API keys, tokens, and other sensitive values
          </p>
        </div>
        <button className={styles.btnAdd} onClick={() => setShowForm(true)}>Add Secret</button>
      </div>

      {secrets.length === 0 ? (
        <EmptyState
          message="No secrets configured"
          actionLabel="Add Secret"
          onAction={() => setShowForm(true)}
        />
      ) : (
        <div className={styles.sourceList}>
          {secrets.map(secret => (
            <SecretCard
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
        </div>
      )}

      {showForm && (
        <SecretForm
          onSubmit={handleCreate}
          onCancel={() => setShowForm(false)}
        />
      )}

      {editingSecret && (
        <SecretForm
          secret={editingSecret}
          onSubmit={handleUpdate}
          onCancel={() => setEditingSecret(null)}
        />
      )}
    </div>
  )
}

interface SecretCardProps {
  secret: Secret
  revealedValue?: string
  isRevealing?: boolean
  onToggleReveal: () => void
  onCopy: () => void
  onEdit: () => void
  onDelete: () => void
}

const SecretCard = ({
  secret,
  revealedValue,
  isRevealing,
  onToggleReveal,
  onCopy,
  onEdit,
  onDelete,
}: SecretCardProps) => {
  return (
    <div className={styles.sourceCard}>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <h4 className="font-medium text-slate-800 font-mono">{secret.name}</h4>
        </div>
        {secret.description && (
          <p className="text-sm text-slate-500 mt-1">{secret.description}</p>
        )}
        <div className="mt-2 flex items-center gap-2">
          <div className="flex-1 font-mono text-sm bg-slate-100 rounded px-2 py-1 overflow-hidden">
            {revealedValue ? (
              <span className="text-slate-800 break-all">{revealedValue}</span>
            ) : (
              <span className="text-slate-400">{'*'.repeat(24)}</span>
            )}
          </div>
          <button
            onClick={onToggleReveal}
            disabled={isRevealing}
            className="p-1.5 text-slate-500 hover:text-slate-700 hover:bg-slate-100 rounded"
            title={revealedValue ? 'Hide value' : 'Reveal value'}
          >
            {isRevealing ? (
              <LoadingIcon />
            ) : revealedValue ? (
              <EyeOffIcon />
            ) : (
              <EyeIcon />
            )}
          </button>
          <button
            onClick={onCopy}
            className="p-1.5 text-slate-500 hover:text-slate-700 hover:bg-slate-100 rounded"
            title="Copy to clipboard"
          >
            <CopyIcon />
          </button>
        </div>
        <p className="text-xs text-slate-400 mt-2">
          Updated {new Date(secret.updated_at).toLocaleDateString()}
        </p>
      </div>
      <div className="flex items-start gap-1 ml-4">
        <button
          onClick={onEdit}
          className={styles.btnEdit}
          title="Edit secret"
        >
          Edit
        </button>
        <button
          onClick={onDelete}
          className={styles.btnDelete}
          title="Delete secret"
        >
          Delete
        </button>
      </div>
    </div>
  )
}

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
    <Modal title={secret ? 'Edit Secret' : 'Add Secret'} onClose={onCancel}>
      <form onSubmit={handleSubmit} className={styles.form}>
        {error && <div className={styles.formError}>{error}</div>}

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Name</label>
          <input
            type="text"
            value={formData.name}
            onChange={e => setFormData({ ...formData, name: e.target.value })}
            required={!secret}
            disabled={!!secret}
            placeholder="api-key or github-token"
            className={`${styles.formInput} font-mono`}
          />
          <p className="text-xs text-slate-500 mt-1">
            Must be a valid identifier (letters, digits, and -_*+!?'&lt;&gt;=)
          </p>
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>
            {secret ? 'New Value (leave empty to keep current)' : 'Value'}
          </label>
          <input
            type="password"
            value={formData.value}
            onChange={e => setFormData({ ...formData, value: e.target.value })}
            required={!secret}
            placeholder={secret ? 'Enter new value to update' : 'Enter secret value'}
            className={`${styles.formInput} font-mono`}
            autoComplete="off"
          />
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Description (optional)</label>
          <textarea
            value={formData.description}
            onChange={e => setFormData({ ...formData, description: e.target.value })}
            rows={2}
            placeholder="What is this secret used for?"
            className={styles.formTextarea}
          />
        </div>

        <div className={styles.formActions}>
          <button type="button" className={styles.btnCancel} onClick={onCancel}>Cancel</button>
          <button type="submit" className={styles.btnSubmit} disabled={submitting}>
            {submitting ? 'Saving...' : 'Save'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

export default SecretsPanel

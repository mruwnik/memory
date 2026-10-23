import { useState } from 'react'
import type { EmailAccountTestResult } from '../../hooks/useSources'
import { Button, ConfirmDialog, Input } from '@/components/shared/ui'

// Format relative time
export const formatRelativeTime = (dateString: string | null): string => {
  if (!dateString) return 'Never'
  const date = new Date(dateString)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMins / 60)
  const diffDays = Math.floor(diffHours / 24)

  if (diffMins < 1) return 'Just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  return date.toLocaleDateString()
}

// Status Badge
interface StatusBadgeProps {
  active: boolean
  onClick?: () => void
}

export const StatusBadge = ({ active, onClick }: StatusBadgeProps) => {
  const className = `px-2 py-1 rounded text-xs font-medium ${
    active ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-500'
  }`

  if (onClick) {
    return (
      <button
        type="button"
        role="switch"
        aria-checked={active}
        className={`${className} cursor-pointer hover:opacity-80`}
        onClick={onClick}
      >
        {active ? 'Active' : 'Inactive'}
      </button>
    )
  }

  return <span className={className}>{active ? 'Active' : 'Inactive'}</span>
}

// Sync Status
interface SyncStatusProps {
  lastSyncAt: string | null
  syncError?: string | null
}

export const SyncStatus = ({ lastSyncAt, syncError }: SyncStatusProps) => (
  <div className="flex items-center gap-2 text-sm text-slate-500 my-2">
    <span>Last sync: {formatRelativeTime(lastSyncAt)}</span>
    {syncError && (
      <span className="text-red-600 cursor-help" title={syncError}>
        Error
      </span>
    )}
  </div>
)

// Sync Button
interface SyncButtonProps {
  onSync: () => Promise<void>
  disabled?: boolean
  label?: string
}

export const SyncButton = ({ onSync, disabled, label = 'Sync' }: SyncButtonProps) => {
  const [syncing, setSyncing] = useState(false)

  const handleSync = async () => {
    setSyncing(true)
    try {
      await onSync()
    } finally {
      setSyncing(false)
    }
  }

  return (
    <Button variant="primary" size="sm"
      onClick={handleSync}
      disabled={disabled || syncing}
    >
      {syncing ? 'Syncing...' : label}
    </Button>
  )
}

// Test Connection Button (with inline result)
interface TestConnectionButtonProps {
  onTest: () => Promise<EmailAccountTestResult>
  onResult?: (result: EmailAccountTestResult) => void
  disabled?: boolean
}

export const TestConnectionButton = ({ onTest, onResult, disabled }: TestConnectionButtonProps) => {
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<{ status: string; message: string } | null>(null)

  const handleTest = async () => {
    setTesting(true)
    setResult(null)
    try {
      const res = await onTest()
      setResult(res)
      onResult?.(res)
    } catch (e) {
      setResult({ status: 'error', message: e instanceof Error ? e.message : 'Connection failed' })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="space-y-2 my-2">
      <Button size="sm"
        type="button"
        onClick={handleTest}
        disabled={disabled || testing}
      >
        {testing ? 'Testing…' : 'Test Connection'}
      </Button>
      {result && (
        <div
          className={`py-2 px-3 rounded text-sm ${
            result.status === 'success' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
          }`}
        >
          {result.message}
        </div>
      )}
    </div>
  )
}

// Tags Input
interface TagsInputProps {
  tags: string[]
  onChange: (tags: string[]) => void
  placeholder?: string
}

export const TagsInput = ({ tags, onChange, placeholder = 'Add tag...' }: TagsInputProps) => {
  const [input, setInput] = useState('')

  const addTag = () => {
    const trimmed = input.trim()
    if (trimmed && !tags.includes(trimmed)) {
      onChange([...tags, trimmed])
      setInput('')
    }
  }

  const removeTag = (tag: string) => {
    onChange(tags.filter(t => t !== tag))
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      addTag()
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1">
        {tags.map(tag => (
          <span key={tag} className="bg-slate-100 text-slate-700 px-2 py-1 rounded text-sm flex items-center gap-1">
            {tag}
            <button
              type="button"
              onClick={() => removeTag(tag)}
              className="text-slate-400 hover:text-slate-600"
            >
              &times;
            </button>
          </span>
        ))}
      </div>
      <Input
        type="text"
        value={input}
        onChange={e => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={addTag}
        placeholder={placeholder}
      />
    </div>
  )
}

// Interval Input (minutes)
interface IntervalInputProps {
  value: number
  onChange: (value: number) => void
  label?: string
}

export const IntervalInput = ({ value, onChange, label = 'Check interval' }: IntervalInputProps) => {
  const hours = Math.floor(value / 60)
  const minutes = value % 60

  const setHours = (h: number) => onChange(h * 60 + minutes)
  const setMinutes = (m: number) => onChange(hours * 60 + m)

  return (
    <div className="space-y-1">
      <label className="text-sm font-medium text-slate-700">{label}</label>
      <div className="flex items-center gap-2">
        <Input
          type="number"
          min="0"
          value={hours}
          onChange={e => setHours(parseInt(e.target.value) || 0)}
          className="w-16 text-center"
        />
        <span className="text-slate-500">h</span>
        <Input
          type="number"
          min="0"
          max="59"
          value={minutes}
          onChange={e => setMinutes(parseInt(e.target.value) || 0)}
          className="w-16 text-center"
        />
        <span className="text-slate-500">m</span>
      </div>
    </div>
  )
}

// Source Card wrapper
interface SourceCardProps {
  title: string
  subtitle?: string
  active: boolean
  lastSyncAt: string | null
  syncError?: string | null
  onToggleActive?: () => void
  onEdit?: () => void
  onDelete?: () => void
  onSync?: () => Promise<void>
  additionalActions?: React.ReactNode
  children?: React.ReactNode
}

export const SourceCard = ({
  title,
  subtitle,
  active,
  lastSyncAt,
  syncError,
  onToggleActive,
  onEdit,
  onDelete,
  onSync,
  additionalActions,
  children,
}: SourceCardProps) => {
  const [confirmDelete, setConfirmDelete] = useState(false)

  return (
    <div className={`bg-white p-4 rounded-lg shadow-sm border-l-4 ${active ? 'border-green-500' : 'border-slate-300 opacity-60'}`}>
      <div className="flex items-start justify-between mb-2">
        <div className="flex-1 min-w-0">
          <h4 className="font-semibold text-slate-800 truncate">{title}</h4>
          {subtitle && <p className="text-sm text-slate-500 truncate">{subtitle}</p>}
        </div>
        <StatusBadge active={active} onClick={onToggleActive} />
      </div>

      <SyncStatus lastSyncAt={lastSyncAt} syncError={syncError} />

      {children}

      <div className="flex gap-2 mt-3 flex-wrap">
        {onSync && <SyncButton onSync={onSync} disabled={!active} />}
        {additionalActions}
        {onEdit && (
          <Button size="sm"
            onClick={onEdit}
          >
            Edit
          </Button>
        )}
        {onDelete && (
          <Button variant="danger" size="sm"
            onClick={() => setConfirmDelete(true)}
          >
            Delete
          </Button>
        )}
      </div>

      {confirmDelete && (
        <ConfirmDialog
          message={`Are you sure you want to delete "${title}"?`}
          onConfirm={() => {
            onDelete?.()
            setConfirmDelete(false)
          }}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </div>
  )
}

import { useState } from 'react'

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

export const StatusBadge = ({ active, onClick }: StatusBadgeProps) => (
  <span
    className={`px-2 py-1 rounded text-xs font-medium ${
      active
        ? 'bg-green-100 text-green-700'
        : 'bg-slate-100 text-slate-500'
    } ${onClick ? 'cursor-pointer hover:opacity-80' : ''}`}
    onClick={onClick}
  >
    {active ? 'Active' : 'Inactive'}
  </span>
)

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
    <button
      className="bg-primary text-white py-1.5 px-3 rounded text-sm hover:bg-primary-dark disabled:bg-slate-300 disabled:cursor-not-allowed"
      onClick={handleSync}
      disabled={disabled || syncing}
    >
      {syncing ? 'Syncing...' : label}
    </button>
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
      <input
        type="text"
        value={input}
        onChange={e => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={addTag}
        placeholder={placeholder}
        className="w-full py-2 px-3 border border-slate-200 rounded text-sm focus:border-primary focus:outline-none"
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
        <input
          type="number"
          min="0"
          value={hours}
          onChange={e => setHours(parseInt(e.target.value) || 0)}
          className="w-16 py-2 px-2 border border-slate-200 rounded text-sm text-center"
        />
        <span className="text-slate-500">h</span>
        <input
          type="number"
          min="0"
          max="59"
          value={minutes}
          onChange={e => setMinutes(parseInt(e.target.value) || 0)}
          className="w-16 py-2 px-2 border border-slate-200 rounded text-sm text-center"
        />
        <span className="text-slate-500">m</span>
      </div>
    </div>
  )
}

// Confirm Dialog
interface ConfirmDialogProps {
  message: string
  onConfirm: () => void
  onCancel: () => void
}

export const ConfirmDialog = ({ message, onConfirm, onCancel }: ConfirmDialogProps) => (
  <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
    <div className="bg-white rounded-xl shadow-xl p-6 max-w-sm w-full">
      <p className="text-slate-800 mb-4">{message}</p>
      <div className="flex justify-end gap-2">
        <button
          className="py-2 px-4 border border-slate-200 rounded text-slate-600 hover:bg-slate-50"
          onClick={onCancel}
        >
          Cancel
        </button>
        <button
          className="py-2 px-4 bg-red-600 text-white rounded hover:bg-red-700"
          onClick={onConfirm}
        >
          Confirm
        </button>
      </div>
    </div>
  </div>
)

// Modal wrapper
interface ModalProps {
  title: string
  onClose: () => void
  children: React.ReactNode
  className?: string
}

export const Modal = ({ title, onClose, children, className }: ModalProps) => (
  <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
    <div
      className={`bg-white rounded-xl shadow-xl max-w-lg w-full max-h-[80vh] overflow-auto ${className || ''}`}
      onClick={e => e.stopPropagation()}
    >
      <div className="flex items-center justify-between p-4 border-b border-slate-100">
        <h3 className="text-lg font-semibold text-slate-800">{title}</h3>
        <button
          className="text-slate-400 hover:text-slate-600 text-2xl leading-none"
          onClick={onClose}
        >
          &times;
        </button>
      </div>
      <div className="p-4">
        {children}
      </div>
    </div>
  </div>
)

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
          <button
            className="py-1.5 px-3 border border-slate-200 rounded text-sm text-slate-600 hover:bg-slate-50"
            onClick={onEdit}
          >
            Edit
          </button>
        )}
        {onDelete && (
          <button
            className="py-1.5 px-3 border border-red-200 rounded text-sm text-red-600 hover:bg-red-50"
            onClick={() => setConfirmDelete(true)}
          >
            Delete
          </button>
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

// Empty state
interface EmptyStateProps {
  message: string
  actionLabel?: string
  onAction?: () => void
}

export const EmptyState = ({ message, actionLabel, onAction }: EmptyStateProps) => (
  <div className="text-center py-12 text-slate-500">
    <p className="mb-4">{message}</p>
    {actionLabel && onAction && (
      <button
        className="bg-primary text-white py-2 px-4 rounded hover:bg-primary-dark"
        onClick={onAction}
      >
        {actionLabel}
      </button>
    )}
  </div>
)

// Loading state
export const LoadingState = () => (
  <div className="flex flex-col items-center justify-center py-12 text-slate-500">
    <div className="w-6 h-6 border-2 border-slate-200 border-t-primary rounded-full animate-spin mb-2"></div>
    <p>Loading...</p>
  </div>
)

// Error state
interface ErrorStateProps {
  message: string
  onRetry?: () => void
}

export const ErrorState = ({ message, onRetry }: ErrorStateProps) => (
  <div className="text-center py-12 text-red-600">
    <p className="mb-4">{message}</p>
    {onRetry && (
      <button
        className="bg-primary text-white py-2 px-4 rounded hover:bg-primary-dark"
        onClick={onRetry}
      >
        Retry
      </button>
    )}
  </div>
)

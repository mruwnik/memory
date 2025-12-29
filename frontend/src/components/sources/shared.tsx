import { useState, useCallback } from 'react'

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
    className={`status-badge ${active ? 'active' : 'inactive'}`}
    onClick={onClick}
    style={{ cursor: onClick ? 'pointer' : 'default' }}
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
  <div className="sync-status">
    <span className="sync-time">
      Last sync: {formatRelativeTime(lastSyncAt)}
    </span>
    {syncError && <span className="sync-error" title={syncError}>Error</span>}
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
      className="sync-btn"
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
    <div className="tags-input">
      <div className="tags-list">
        {tags.map(tag => (
          <span key={tag} className="tag">
            {tag}
            <button type="button" onClick={() => removeTag(tag)}>&times;</button>
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
        className="tag-input"
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
    <div className="interval-input">
      <label>{label}</label>
      <div className="interval-controls">
        <input
          type="number"
          min="0"
          value={hours}
          onChange={e => setHours(parseInt(e.target.value) || 0)}
        />
        <span>h</span>
        <input
          type="number"
          min="0"
          max="59"
          value={minutes}
          onChange={e => setMinutes(parseInt(e.target.value) || 0)}
        />
        <span>m</span>
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
  <div className="confirm-dialog-overlay">
    <div className="confirm-dialog">
      <p>{message}</p>
      <div className="confirm-dialog-buttons">
        <button className="cancel-btn" onClick={onCancel}>Cancel</button>
        <button className="confirm-btn" onClick={onConfirm}>Confirm</button>
      </div>
    </div>
  </div>
)

// Modal wrapper
interface ModalProps {
  title: string
  onClose: () => void
  children: React.ReactNode
}

export const Modal = ({ title, onClose, children }: ModalProps) => (
  <div className="modal-overlay" onClick={onClose}>
    <div className="modal" onClick={e => e.stopPropagation()}>
      <div className="modal-header">
        <h3>{title}</h3>
        <button className="modal-close" onClick={onClose}>&times;</button>
      </div>
      <div className="modal-content">
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
  children,
}: SourceCardProps) => {
  const [confirmDelete, setConfirmDelete] = useState(false)

  return (
    <div className={`source-card ${active ? '' : 'inactive'}`}>
      <div className="source-card-header">
        <div className="source-card-info">
          <h4>{title}</h4>
          {subtitle && <p className="source-subtitle">{subtitle}</p>}
        </div>
        <StatusBadge active={active} onClick={onToggleActive} />
      </div>

      <SyncStatus lastSyncAt={lastSyncAt} syncError={syncError} />

      {children}

      <div className="source-card-actions">
        {onSync && <SyncButton onSync={onSync} disabled={!active} />}
        {onEdit && <button className="edit-btn" onClick={onEdit}>Edit</button>}
        {onDelete && (
          <button className="delete-btn" onClick={() => setConfirmDelete(true)}>Delete</button>
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
  <div className="empty-state">
    <p>{message}</p>
    {actionLabel && onAction && (
      <button className="add-btn" onClick={onAction}>{actionLabel}</button>
    )}
  </div>
)

// Loading state
export const LoadingState = () => (
  <div className="loading-state">
    <div className="loading-spinner"></div>
    <p>Loading...</p>
  </div>
)

// Error state
interface ErrorStateProps {
  message: string
  onRetry?: () => void
}

export const ErrorState = ({ message, onRetry }: ErrorStateProps) => (
  <div className="error-state">
    <p>{message}</p>
    {onRetry && <button className="retry-btn" onClick={onRetry}>Retry</button>}
  </div>
)

import { useState } from 'react'
import type { EmailAccountTestResult, SyncStatus } from '../../hooks/useSources'
import { Badge, Button, ConfirmButton, Input, Notice, Row, RowError, Select } from '@/components/shared/ui'

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

export const StatusBadge = ({ active }: { active: boolean }) => (
  <Badge tone={active ? 'success' : 'neutral'}>{active ? 'Active' : 'Inactive'}</Badge>
)

const failingSince = (status: SyncStatus) =>
  status.sync_failing_since ? `Sync failing since ${new Date(status.sync_failing_since).toLocaleString()}` : 'Sync failing'

/** Flags a source whose latest sync failed, for a Row's badges. */
export const SyncFailingBadge = ({ status }: { status: SyncStatus }) =>
  status.sync_error ? <Badge tone="danger" title={failingSince(status)}>Sync failing</Badge> : null

/** The latest sync error and when the failing streak began, for a Row's body. */
export const SyncFailure = ({ status }: { status: SyncStatus }) =>
  status.sync_error ? <RowError>{failingSince(status)}: {status.sync_error}</RowError> : null

/** The row action that stops or restarts collection for a source shown with a StatusBadge. */
export const PauseButton = ({ active, onClick }: { active: boolean; onClick: () => void }) => (
  <Button size="sm" onClick={onClick}>{active ? 'Pause' : 'Resume'}</Button>
)

// Sync Button
interface SyncButtonProps {
  onSync: () => Promise<unknown>
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
    <Button size="sm"
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
    <div className="space-y-2">
      <Button size="sm"
        type="button"
        onClick={handleTest}
        disabled={disabled || testing}
      >
        {testing ? 'Testing…' : 'Test connection'}
      </Button>
      {result && <Notice tone={result.status === 'success' ? 'success' : 'error'}>{result.message}</Notice>}
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
              className="text-slate-500 hover:text-slate-600"
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

// A connected source in a panel's List
interface SourceRowProps {
  title: string
  subtitle?: string
  /** Facts after the subtitle and last sync time. */
  meta?: React.ReactNode[]
  active: boolean
  lastSyncAt: string | null
  sync?: SyncStatus
  onToggleActive?: () => void
  onEdit?: () => void
  onDelete?: () => void
  onSync?: () => Promise<unknown>
  additionalActions?: React.ReactNode
  children?: React.ReactNode
}

export const SourceRow = ({
  title,
  subtitle,
  meta = [],
  active,
  lastSyncAt,
  sync,
  onToggleActive,
  onEdit,
  onDelete,
  onSync,
  additionalActions,
  children,
}: SourceRowProps) => (
  <Row
    title={title}
    muted={!active}
    badges={<><StatusBadge active={active} />{sync && <SyncFailingBadge status={sync} />}</>}
    meta={[subtitle, `Last sync: ${formatRelativeTime(lastSyncAt)}`, ...meta]}
    actions={<>
      {onSync && <SyncButton onSync={onSync} disabled={!active} />}
      {onToggleActive && <PauseButton active={active} onClick={onToggleActive} />}
      {additionalActions}
      {onEdit && <Button size="sm" onClick={onEdit}>Edit</Button>}
      {onDelete && <ConfirmButton confirm={`Are you sure you want to delete "${title}"?`} onConfirm={onDelete} />}
    </>}
  >
    {sync && <SyncFailure status={sync} />}
    {children}
  </Row>
)

const SENSITIVITIES = ['public', 'basic', 'internal', 'confidential'] as const

/** Project and sensitivity pickers for a chat workspace, server or channel row. */
export const AccessSelects = ({ projectId, sensitivity, projects, disabled, onProject, onSensitivity }: {
  projectId: number | null | undefined
  sensitivity: string
  projects: { id: number; title: string }[]
  disabled: boolean
  onProject: (id: number | undefined) => void
  onSensitivity: (s: typeof SENSITIVITIES[number]) => void
}) => (
  <>
    <Select className="w-auto py-1 text-xs" value={projectId || ''} disabled={disabled} title="Project" aria-label="Project"
      onChange={e => onProject(e.target.value ? parseInt(e.target.value) : undefined)}>
      <option value="">No project</option>
      {projects.map(p => <option key={p.id} value={p.id}>{p.title}</option>)}
    </Select>
    <Select className="w-auto py-1 text-xs" value={sensitivity} disabled={disabled} title="Sensitivity" aria-label="Sensitivity"
      onChange={e => onSensitivity(e.target.value as typeof SENSITIVITIES[number])}>
      {SENSITIVITIES.map(v => <option key={v} value={v}>{v.charAt(0).toUpperCase() + v.slice(1)}</option>)}
    </Select>
  </>
)

import { useState, type ComponentProps, type RefObject } from 'react'
import type { EmailAccountTestResult, SyncStatus } from '../../hooks/useSources'
import { useModule } from '@/hooks/useModule'
import { formatUserDate, formatUserDateTime } from '@/utils/timezones'
import { Badge, Button, ConfirmButton, Field, Input, Notice, Row, RowError, Select, Fieldset, NewTabLink } from '@/components/shared/ui'

/** A number input's state: '' while cleared, so editing never snaps back to a default. */
export const numberValue = (value: string): number | '' => value === '' ? '' : Number(value)

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
  return formatUserDate(date)
}

export const StatusBadge = ({ active }: { active: boolean }) => (
  <Badge tone={active ? 'success' : 'neutral'}>{active ? 'Active' : 'Inactive'}</Badge>
)

const SHORT_ERROR = 200

const failingSince = (since: string | null) => {
  if (!since) return 'Sync failing'
  const at = new Date(since)
  const ago = formatRelativeTime(since)
  // Beyond a week formatRelativeTime gives the bare date, which `at` already shows.
  const suffix = ago === formatUserDate(at) ? '' : ` (${ago})`
  return `Sync failing since ${formatUserDateTime(at)}${suffix}`
}

/** Flags a source whose latest sync failed, for a Row's badges. */
export const SyncFailingBadge = ({ status }: { status: SyncStatus }) =>
  status.sync_error ? <Badge tone="danger">Sync failing</Badge> : null

/** The latest sync error and when the failing streak began, for a Row's body. */
export const SyncFailure = ({ status }: { status: SyncStatus }) => {
  const [full, setFull] = useState(false)
  const error = status.sync_error
  if (!error) return null
  // Errors run to 1000 characters and would otherwise bury the row's actions.
  const long = error.length > SHORT_ERROR
  return (
    <RowError>
      {failingSince(status.sync_failing_since)}: {long && !full ? `${error.slice(0, SHORT_ERROR)}…` : error}
      {long && <> <Button variant="link" size="sm" onClick={() => setFull(!full)}>{full ? 'Show less' : 'Show full error'}</Button></>}
    </RowError>
  )
}

/** The row action that stops or restarts collection for a source shown with a StatusBadge. */
export const PauseButton = ({ active, onClick, disabled }: { active: boolean; onClick: () => void; disabled?: boolean }) => (
  <Button size="sm" onClick={onClick} disabled={disabled}>{active ? 'Pause' : 'Resume'}</Button>
)

const COLLECT_CHOICES = { inherit: null, collect: true, skip: false } as const

/** A chat channel's collection override: inherit the workspace/server setting, or force it on or off. */
export const CollectSelect = ({ value, inherited, disabled, onChange }: {
  value: boolean | null
  inherited: boolean
  disabled?: boolean
  onChange: (value: boolean | null) => void
}) => (
  <Select className="w-auto py-1 text-xs" aria-label="Collect messages" title="Collect messages" disabled={disabled}
    value={value === null ? 'inherit' : value ? 'collect' : 'skip'}
    onChange={e => onChange(COLLECT_CHOICES[e.target.value as keyof typeof COLLECT_CHOICES])}>
    <option value="inherit">Inherit ({inherited ? 'collect' : 'skip'})</option>
    <option value="collect">Collect</option>
    <option value="skip">Skip</option>
  </Select>
)

/**
 * SubList `empty` and `footer` for a nested list loaded on expand: `items` is undefined while
 * loading and an Error when the load failed.
 */
export const lazySubList = (items: unknown[] | Error | undefined, noun: string, onRetry: () => void) => ({
  empty: items === undefined ? `Loading ${noun}...` : !(items instanceof Error) && `No ${noun}`,
  footer: items instanceof Error && <Notice tone="error" onRetry={onRetry}>Couldn't load {noun}: {items.message}</Notice>,
})

/**
 * Opens a popup for a URL that is only known after an await. Call it inside the click handler:
 * browsers allow a popup only during the click, so the window opens blank first. Resolves to the
 * URL when the browser blocked the popup, for the caller to offer as a link. `noopener` can't be
 * a window feature here (window.open would return null), so a popup that doesn't need its opener
 * passes keepOpener = false to drop it before navigating.
 */
export const openPopup = async (getUrl: () => Promise<string>, features = 'width=600,height=700', keepOpener = true) => {
  const popup = window.open('', '_blank', features)
  let url: string
  try {
    url = await getUrl()
  } catch (e) {
    popup?.close()
    throw e
  }
  if (popup && !popup.closed) {
    if (!keepOpener) popup.opener = null
    popup.location.href = url
  }
  return { blockedUrl: popup ? null : url }
}

/** Offers a blocked popup's URL as a link. */
export const PopupBlockedNotice = ({ url, label, onDismiss }: { url: string | null; label: string; onDismiss: () => void }) => url ? (
  <Notice tone="warning" onDismiss={onDismiss}>
    Your browser blocked the popup. <NewTabLink href={url} className="underline font-medium">{label}</NewTabLink>
  </Notice>
) : null

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
  /** Set by Field so its label and hint reach the text input. */
  id?: string
  'aria-describedby'?: string
}

export const TagsInput = ({ tags, onChange, placeholder = 'Add tag...', id, 'aria-describedby': describedBy }: TagsInputProps) => {
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
              aria-label={`Remove tag ${tag}`}
              className="text-slate-500 hover:text-slate-600"
            >
              &times;
            </button>
          </span>
        ))}
      </div>
      <Input
        id={id}
        aria-describedby={describedBy}
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
    <Fieldset legend={label}>
      <div className="flex items-center gap-2">
        <Input
          type="number"
          min="0"
          aria-label="Hours"
          value={hours}
          onChange={e => setHours(parseInt(e.target.value) || 0)}
          className="w-16 text-center"
        />
        <span aria-hidden="true" className="text-slate-500">h</span>
        <Input
          type="number"
          min="0"
          max="59"
          aria-label="Minutes"
          value={minutes}
          onChange={e => setMinutes(parseInt(e.target.value) || 0)}
          className="w-16 text-center"
        />
        <span aria-hidden="true" className="text-slate-500">m</span>
      </div>
    </Fieldset>
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
  /** Feeds record attempts, not successes, so they say "Last checked". */
  lastSyncLabel?: string
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
  lastSyncLabel = 'Last sync',
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
    meta={[subtitle, `${lastSyncLabel}: ${formatRelativeTime(lastSyncAt)}`, ...meta]}
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

/** The sensitivities every source accepts. Email accounts also accept "hidden", passed as an extra option. */
export const SENSITIVITIES = ['public', 'basic', 'internal', 'confidential'] as const
export type Sensitivity = typeof SENSITIVITIES[number]

type SelectProps = Omit<ComponentProps<'select'>, 'value' | 'onChange'>
type AccessProject = { id: number; title: string; repo_path?: string | null }

/** Picks a source's project; `Field` wires its label. */
export const ProjectSelect = ({ value, projects, onChange, ...props }: SelectProps & {
  value: number | null | undefined
  projects: AccessProject[]
  onChange: (id: number | undefined) => void
}) => (
  <Select {...props} value={value || ''} onChange={e => onChange(e.target.value ? parseInt(e.target.value) : undefined)}>
    <option value="">No project</option>
    {projects.map(p => <option key={p.id} value={p.id}>{p.repo_path ? `${p.title} (${p.repo_path})` : p.title}</option>)}
  </Select>
)

/** Picks a source's sensitivity; `children` adds options beyond SENSITIVITIES. */
export const SensitivitySelect = <T extends string = Sensitivity>({ value, onChange, children, ...props }: SelectProps & {
  value: T
  onChange: (value: T) => void
}) => (
  <Select {...props} value={value} onChange={e => onChange(e.target.value as T)}>
    {SENSITIVITIES.map(v => <option key={v} value={v}>{v.charAt(0).toUpperCase() + v.slice(1)}</option>)}
    {children}
  </Select>
)

/** Wraps an async action with a flag that is true while it runs, to disable the controls that started it. */
export const useBusy = <A extends unknown[]>(action: (...args: A) => Promise<unknown>) => {
  const [busy, setBusy] = useState(false)
  const run = async (...args: A) => {
    setBusy(true)
    try {
      await action(...args)
    } finally {
      setBusy(false)
    }
  }
  return [busy, run] as const
}

/** Inline project and sensitivity pickers for a row; "No project" sends project_id: null. */
export const AccessSelects = ({ projectId, sensitivity, projects, disabled, onChange }: {
  projectId: number | null | undefined
  sensitivity: string
  projects: AccessProject[]
  disabled: boolean
  onChange: (updates: { project_id?: number | null; sensitivity?: Sensitivity }) => void
}) => {
  const teamsOn = useModule('teams')
  return (
  <>
    {teamsOn && (
      <ProjectSelect className="w-auto py-1 text-xs" value={projectId} projects={projects} disabled={disabled} title="Project" aria-label="Project"
        onChange={id => onChange({ project_id: id ?? null })} />
    )}
    <SensitivitySelect className="w-auto py-1 text-xs" value={sensitivity as Sensitivity} disabled={disabled} title="Sensitivity" aria-label="Sensitivity"
      onChange={s => onChange({ sensitivity: s })} />
  </>
  )
}

interface FileButtonProps extends Pick<ComponentProps<typeof Button>, 'variant' | 'size'> {
  inputRef: RefObject<HTMLInputElement | null>
  label: string
  busy?: boolean
  accept: string
  multiple?: boolean
  onChange: (e: React.ChangeEvent<HTMLInputElement>) => void
}

/** A real button that opens a hidden file picker, so uploads work from the keyboard. */
export const FileButton = ({ inputRef, label, busy, accept, multiple, onChange, variant = 'primary', size }: FileButtonProps) => (
  <>
    <Button variant={variant} size={size} onClick={() => inputRef.current?.click()} disabled={busy}>
      {busy ? 'Uploading...' : label}
    </Button>
    <input ref={inputRef} type="file" accept={accept} multiple={multiple} onChange={onChange} className="hidden" tabIndex={-1} />
  </>
)

/** Project picker for an account form; absent while the teams module is off. */
export const ProjectField = ({ hint, ...props }: ComponentProps<typeof ProjectSelect> & { hint: string }) =>
  useModule('teams') ? <Field label="Project" hint={hint}><ProjectSelect {...props} /></Field> : null

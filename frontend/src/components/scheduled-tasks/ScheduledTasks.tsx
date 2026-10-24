import { useState, useEffect, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import { useScheduledTasks, ScheduledTask, TaskExecution, UpdateTaskBody, CreateTaskBody } from '@/hooks/useScheduledTasks'
import { useClaude, Environment } from '@/hooks/useClaude'
import { useDiscord } from '@/hooks/useDiscord'
import { useSlack } from '@/hooks/useSlack'
import { usePeople } from '@/hooks/usePeople'
import Combobox, { ComboOption } from '@/components/shared/Combobox'
import { PauseButton, StatusBadge, formatRelativeTime } from '@/components/sources/shared'
import { Badge, Button, ButtonLink, Checkbox, ConfirmButton, EmptyState, Field, FieldRow, FormActions, Hint, Input, List, LoadingState, Modal, Notice, Page, RefreshButton, Row, RowError, Select, Textarea, ToggleGroup, Toolbar, useActionNotice, type Tone } from '@/components/shared/ui'

type TypeFilter = 'all' | 'notification' | 'claude_session' | 'agent_run'

const TYPE_LABELS: Record<string, string> = {
  claude_session: 'Claude session',
  agent_run: 'Agent run',
}

const EXECUTION_STATUS_TONES: Record<string, Tone> = {
  pending: 'warning',
  running: 'info',
  completed: 'success',
  failed: 'danger',
}

const CHANNELS = ['discord', 'slack', 'email'] as const

function describeCron(expr: string | null): string {
  if (!expr) return 'No schedule'
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return expr

  const [minute, hour, dom, month, dow] = parts

  if (dom === '*' && month === '*') {
    const timeStr = hour !== '*' && minute !== '*'
      ? `at ${hour.padStart(2, '0')}:${minute.padStart(2, '0')} UTC`
      : hour !== '*'
        ? `at ${hour.padStart(2, '0')}:00 UTC`
        : minute !== '*'
          ? `at :${minute.padStart(2, '0')} every hour`
          : ''

    if (dow === '*') {
      if (hour === '*' && minute === '*') return 'Every minute'
      if (hour === '*') return `Every hour at :${minute.padStart(2, '0')}`
      return `Daily ${timeStr}`
    }
    if (dow === '1-5') return `Weekdays ${timeStr}`
    if (dow === '0,6') return `Weekends ${timeStr}`

    const dayNames: Record<string, string> = {
      '0': 'Sun', '1': 'Mon', '2': 'Tue', '3': 'Wed', '4': 'Thu', '5': 'Fri', '6': 'Sat',
    }
    const dayList = dow.split(',').map(d => dayNames[d] || d).join(', ')
    return `${dayList} ${timeStr}`
  }

  return expr
}

function formatFutureTime(dateStr: string | null): string {
  if (!dateStr) return 'Not scheduled'
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = date.getTime() - now.getTime()
  if (diffMs < 0) return formatRelativeTime(dateStr)

  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)

  if (diffMins < 1) return 'any moment'
  if (diffMins < 60) return `in ${diffMins}m`
  if (diffHours < 24) return `in ${diffHours}h`
  if (diffDays < 7) return `in ${diffDays}d`
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

type TaskStatus = 'active' | 'inactive' | 'done'

function getTaskStatus(task: ScheduledTask): TaskStatus {
  if (task.enabled) return 'active'
  if (task.cron_expression) return 'inactive'  // paused recurring — re-enablable
  return 'done'                                 // fired one-off — terminal
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start || !end) return '-'
  const ms = new Date(end).getTime() - new Date(start).getTime()
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}m`
}

// --- Data Display ---

const DataField = ({ label, value }: { label: string; value: string }) => (
  <span className="inline-flex items-center gap-1 text-xs bg-slate-50 border border-slate-200 rounded px-2 py-0.5">
    <span className="text-slate-500">{label}</span>
    <span className="text-slate-700 font-mono">{value}</span>
  </span>
)

const TaskDataFields = ({ task }: { task: ScheduledTask }) => {
  const data = task.data
  if (!data || Object.keys(data).length === 0) return null

  if (task.task_type === 'agent_run') {
    return (
      <div className="mt-1 text-sm text-slate-600">
        <p className="line-clamp-3">{String(data.input ?? '')}</p>
        <Link to={`/ui/agents/${data.definition_id}`} className="text-primary text-xs">Edit on the agent's page</Link>
      </div>
    )
  }

  if (task.task_type === 'claude_session') {
    const config = data.spawn_config as Record<string, unknown> | undefined
    if (!config) return null

    const fields: { label: string; value: string }[] = []
    if (config.environment_id) fields.push({ label: 'env', value: String(config.environment_id) })
    if (config.snapshot_id) fields.push({ label: 'snapshot', value: String(config.snapshot_id) })
    if (config.repo_url) fields.push({ label: 'repo', value: String(config.repo_url).replace(/^https?:\/\/github\.com\//, '') })
    if (config.run_id) fields.push({ label: 'run', value: String(config.run_id) })
    if (config.enable_playwright) fields.push({ label: 'playwright', value: 'enabled' })
    if (config.allowed_tools) {
      const tools = config.allowed_tools as string[]
      fields.push({ label: 'tools', value: tools.length > 3 ? `${tools.slice(0, 3).join(', ')}...` : tools.join(', ') })
    }

    const prompt = config.initial_prompt as string | undefined
    const showPrompt = prompt && prompt.length > 100

    return (
      <>
        {showPrompt && (
          <p className="text-sm text-slate-600 mt-1 line-clamp-3" title={prompt}>{prompt}</p>
        )}
        {fields.length > 0 && (
          <div className="flex gap-2 mt-2 flex-wrap">
            {fields.map(f => <DataField key={f.label} label={f.label} value={f.value} />)}
          </div>
        )}
      </>
    )
  }

  // Notification tasks: show extra data fields
  const fields: { label: string; value: string }[] = []
  if (data.subject) fields.push({ label: 'subject', value: String(data.subject) })
  if (data.from_address) fields.push({ label: 'from', value: String(data.from_address) })
  if (data.discord_bot_id) fields.push({ label: 'bot', value: String(data.discord_bot_id) })

  if (fields.length === 0) return null
  return (
    <div className="flex gap-2 mt-2 flex-wrap">
      {fields.map(f => <DataField key={f.label} label={f.label} value={f.value} />)}
    </div>
  )
}

// --- Edit Form ---

interface EditFormProps {
  task: ScheduledTask
  onSave: (updates: UpdateTaskBody) => Promise<void>
  onCancel: () => void
  saving: boolean
  error: string | null
}


// --- Reusable field-group components (also used by CreateForm) ---

interface NotificationFieldsProps {
  channel: string
  target: string
  onChannel: (v: string) => void
  onTarget: (v: string) => void
}

type TargetMode = 'channel' | 'dm'

function slackTargetIsDm(target: string): boolean {
  return /^[UW]/i.test(target)
}

const NotificationFields = ({ channel, target, onChannel, onTarget }: NotificationFieldsProps) => {
  const { listChannels: listDiscordChannels, listServers } = useDiscord()
  const { listWorkspaces, listChannels: listSlackChannels } = useSlack()
  const { listPeople } = usePeople()

  const [mode, setMode] = useState<TargetMode>('channel')
  const [channelOptions, setChannelOptions] = useState<ComboOption[]>([])
  const [peopleOptions, setPeopleOptions] = useState<ComboOption[]>([])
  const [loadingChannels, setLoadingChannels] = useState(false)
  const [loadingPeople, setLoadingPeople] = useState(false)
  const didInfer = useRef(false)
  const peopleTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const isChat = channel === 'discord' || channel === 'slack'

  // Load channel options for the selected platform.
  useEffect(() => {
    if (!isChat) {
      setChannelOptions([])
      return
    }
    let cancelled = false
    setLoadingChannels(true)

    const loadDiscord = async (): Promise<ComboOption[]> => {
      const [channels, servers] = await Promise.all([
        listDiscordChannels(),
        listServers().catch(() => []),
      ])
      const serverName = new Map(servers.map(s => [s.id, s.name]))
      return channels.map(c => {
        const group = serverName.get(c.server_id ?? '') ?? c.server_name ?? 'Discord'
        return { value: c.id, label: `${group} / #${c.name}`, group }
      })
    }
    const loadSlack = async (): Promise<ComboOption[]> => {
      const workspaces = await listWorkspaces()
      const perWorkspace = await Promise.all(
        workspaces.map(async ws => {
          const chans = await listSlackChannels(ws.id).catch(() => [])
          return chans.map(c => ({ value: c.id, label: `${ws.name} / #${c.name}`, group: ws.name }))
        })
      )
      return perWorkspace.flat()
    }

    ;(channel === 'discord' ? loadDiscord() : loadSlack())
      .then(opts => { if (!cancelled) setChannelOptions(opts) })
      .catch(() => { if (!cancelled) setChannelOptions([]) })
      .finally(() => { if (!cancelled) setLoadingChannels(false) })

    return () => { cancelled = true }
  }, [channel, isChat, listDiscordChannels, listServers, listWorkspaces, listSlackChannels])

  // Debounced people search (DM mode).
  const searchPeople = useCallback((query: string) => {
    if (peopleTimer.current) clearTimeout(peopleTimer.current)
    setLoadingPeople(true)
    peopleTimer.current = setTimeout(() => {
      listPeople({ search: query || undefined, limit: 20 })
        .then(people =>
          setPeopleOptions(
            people.map(p => ({ value: p.identifier, label: `${p.display_name} (${p.identifier})` }))
          )
        )
        .catch(() => setPeopleOptions([]))
        .finally(() => setLoadingPeople(false))
    }, 250)
  }, [listPeople])

  useEffect(() => {
    if (isChat) searchPeople('')
    return () => { if (peopleTimer.current) clearTimeout(peopleTimer.current) }
  }, [isChat, searchPeople])

  // Infer the DM/Channel mode once for an existing target (edit). Slack uses the
  // id prefix; Discord can't be told from the id, so check the loaded channel list.
  useEffect(() => {
    if (didInfer.current || !isChat) return
    if (!target) { didInfer.current = true; return }
    if (channel === 'slack') {
      setMode(slackTargetIsDm(target) ? 'dm' : 'channel')
      didInfer.current = true
    } else if (!loadingChannels) {
      setMode(channelOptions.some(o => o.value === target) ? 'channel' : 'dm')
      didInfer.current = true
    }
  }, [channel, target, isChat, channelOptions, loadingChannels])

  const handlePlatform = (next: string) => {
    onChannel(next)
    onTarget('')
    setMode('channel')
    didInfer.current = true
  }

  const handleMode = (next: TargetMode) => {
    setMode(next)
    onTarget('')
  }

  return (
    <FieldRow>
      <Field label="Channel">
        <Select value={channel} onChange={e => handlePlatform(e.target.value)}>
          <option value="">Select...</option>
          {CHANNELS.map(c => (
            <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>
          ))}
        </Select>
      </Field>
      <div className="space-y-2">
        <Field label="Target" hint={channel === 'email' && "Email address (or a person's name to look up)."}>
          {!channel && <Input type="text" value="" disabled placeholder="Select a channel first" />}
          {channel === 'email' && (
            <Input type="email" value={target} onChange={e => onTarget(e.target.value)} placeholder="you@example.com" />
          )}
          {isChat && mode === 'channel' && (
            <Combobox value={target} onChange={v => onTarget(v)} options={channelOptions} loading={loadingChannels}
              allowCustom placeholder={`Search ${channel} channels…`} />
          )}
          {isChat && mode === 'dm' && (
            <Combobox value={target} onChange={v => onTarget(v)} options={peopleOptions} loading={loadingPeople}
              onQueryChange={searchPeople} allowCustom placeholder="Search people by name…" />
          )}
        </Field>
        {isChat && (
          <ToggleGroup label="Target type" value={mode} onChange={handleMode}
            options={[{ value: 'channel', label: 'Channel' }, { value: 'dm', label: 'Direct message' }]} />
        )}
      </div>
    </FieldRow>
  )
}

interface SpawnConfigState {
  envId: number | null
  repoUrl: string
  allowedTools: string
  enablePlaywright: boolean
  runId: string
  customEnvText: string
}

// Build a spawn_config object (only set keys) from form state.
function buildSpawnConfig(s: SpawnConfigState): Record<string, unknown> {
  const cfg: Record<string, unknown> = {}
  if (s.envId !== null) cfg.environment_id = s.envId
  if (s.repoUrl) cfg.repo_url = s.repoUrl
  const tools = s.allowedTools.split(',').map(t => t.trim()).filter(Boolean)
  if (tools.length) cfg.allowed_tools = tools
  if (s.enablePlaywright) cfg.enable_playwright = true
  if (s.runId) cfg.run_id = s.runId
  const env: Record<string, string> = {}
  for (const line of s.customEnvText.split('\n')) {
    const t = line.trim()
    const i = t.indexOf('=')
    if (i > 0) env[t.slice(0, i)] = t.slice(i + 1)
  }
  if (Object.keys(env).length) cfg.custom_env = env
  return cfg
}

interface SpawnConfigFieldsProps {
  state: SpawnConfigState
  setState: (updater: (s: SpawnConfigState) => SpawnConfigState) => void
  environments: Environment[]
}

const SpawnConfigFields = ({ state, setState, environments }: SpawnConfigFieldsProps) => (
  <fieldset className="space-y-4 border-t border-slate-200 pt-4">
    <legend className="text-sm font-semibold text-slate-700 pr-2">Spawn config</legend>
    <Field label="Environment">
      <Select value={state.envId ?? ''} onChange={e => setState(s => ({ ...s, envId: e.target.value ? Number(e.target.value) : null }))}>
        <option value="">Select environment...</option>
        {environments.map(env => (
          <option key={env.id} value={env.id}>{env.name}{env.description ? ` - ${env.description}` : ''}</option>
        ))}
      </Select>
    </Field>
    <FieldRow>
      <Field label="Repo URL">
        <Input type="text" value={state.repoUrl} onChange={e => setState(s => ({ ...s, repoUrl: e.target.value }))} placeholder="https://github.com/org/repo" />
      </Field>
      <Field label="Run ID">
        <Input type="text" value={state.runId} onChange={e => setState(s => ({ ...s, runId: e.target.value }))} placeholder="custom-branch-name" />
      </Field>
    </FieldRow>
    <Field label="Allowed tools" hint="Comma-separated tool names">
      <Input type="text" value={state.allowedTools} onChange={e => setState(s => ({ ...s, allowedTools: e.target.value }))} placeholder="Bash, Read, Write, Edit, Grep, Glob" />
    </Field>
    <Field label="Environment variables" hint="One per line: KEY=value">
      <Textarea value={state.customEnvText} onChange={e => setState(s => ({ ...s, customEnvText: e.target.value }))} rows={3} placeholder={"KEY=value\nANOTHER_KEY=value"} className="font-mono" />
    </Field>
    <Checkbox label="Enable Playwright" checked={state.enablePlaywright} onChange={e => setState(s => ({ ...s, enablePlaywright: e.target.checked }))} />
  </fieldset>
)

const envText = (env: unknown) =>
  env ? Object.entries(env as Record<string, string>).map(([k, v]) => `${k}=${v}`).join('\n') : ''

const spawnStateOf = (config: Record<string, unknown>): SpawnConfigState => ({
  envId: (config.environment_id as number) ?? null,
  repoUrl: (config.repo_url as string) || '',
  allowedTools: Array.isArray(config.allowed_tools) ? (config.allowed_tools as string[]).join(', ') : '',
  enablePlaywright: !!config.enable_playwright,
  runId: (config.run_id as string) || '',
  customEnvText: envText(config.custom_env),
})

const EditForm = ({ task, onSave, onCancel, saving, error }: EditFormProps) => {
  const [topic, setTopic] = useState(task.topic || '')
  const [cronExpr, setCronExpr] = useState(task.cron_expression || '')
  const [channel, setChannel] = useState(task.notification_channel || '')
  const [target, setTarget] = useState(task.notification_target || '')

  const isClaude = task.task_type === 'claude_session'
  const config = isClaude ? ((task.data?.spawn_config as Record<string, unknown>) || {}) : {}

  const [message, setMessage] = useState(task.message || '')

  const original = spawnStateOf(config)
  const [spawn, setSpawn] = useState<SpawnConfigState>(original)

  // Load environments for dropdown
  const { listEnvironments } = useClaude()
  const [environments, setEnvironments] = useState<Environment[]>([])
  useEffect(() => {
    if (!isClaude) return
    listEnvironments().then(setEnvironments).catch(() => {})
  }, [isClaude, listEnvironments])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const updates: UpdateTaskBody = {}
    if (topic !== (task.topic || '')) updates.topic = topic
    if (cronExpr !== (task.cron_expression || '')) updates.cron_expression = cronExpr
    if (message !== (task.message || '')) updates.message = message

    if (isClaude) {
      const configUpdates: Record<string, unknown> = {}
      if (spawn.envId !== original.envId) configUpdates.environment_id = spawn.envId
      if (spawn.repoUrl !== original.repoUrl) configUpdates.repo_url = spawn.repoUrl || null
      if (spawn.allowedTools !== original.allowedTools) {
        const tools = spawn.allowedTools.split(',').map(t => t.trim()).filter(Boolean)
        configUpdates.allowed_tools = tools.length > 0 ? tools : null
      }
      if (spawn.enablePlaywright !== original.enablePlaywright) configUpdates.enable_playwright = spawn.enablePlaywright || null
      if (spawn.runId !== original.runId) configUpdates.run_id = spawn.runId || null
      if (spawn.customEnvText !== original.customEnvText) {
        const env = buildSpawnConfig({ ...spawn }).custom_env
        configUpdates.custom_env = env ?? null
      }

      if (Object.keys(configUpdates).length > 0) updates.spawn_config = configUpdates
    } else {
      if (channel !== (task.notification_channel || '')) updates.notification_channel = channel
      if (target !== (task.notification_target || '')) updates.notification_target = target
    }

    if (Object.keys(updates).length === 0) {
      onCancel()
      return
    }
    await onSave(updates)
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <Field label="Topic">
        <Input type="text" value={topic} onChange={e => setTopic(e.target.value)} />
      </Field>
      <Field label="Cron expression" hint={describeCron(cronExpr)}>
        <Input type="text" value={cronExpr} onChange={e => setCronExpr(e.target.value)} placeholder="0 9 * * *" className="font-mono" />
      </Field>
      <Field label={isClaude ? 'Initial prompt' : 'Message'}>
        <Textarea value={message} onChange={e => setMessage(e.target.value)} rows={isClaude ? 4 : 2} />
      </Field>
      {isClaude && <SpawnConfigFields state={spawn} setState={setSpawn} environments={environments} />}
      {task.task_type === 'notification' && (
        <NotificationFields channel={channel} target={target} onChannel={setChannel} onTarget={setTarget} />
      )}
      {error && <Notice tone="error">{error}</Notice>}
      <FormActions>
        <Button type="button" onClick={onCancel} disabled={saving}>Cancel</Button>
        <Button variant="primary" type="submit" disabled={saving}>{saving ? 'Saving...' : 'Save'}</Button>
      </FormActions>
    </form>
  )
}

// --- Create Form ---

interface CreateFormProps {
  onCreate: (body: CreateTaskBody) => Promise<void>
  onCancel: () => void
  saving: boolean
  error: string | null
}

const CreateForm = ({ onCreate, onCancel, saving, error }: CreateFormProps) => {
  const [taskType, setTaskType] = useState<'notification' | 'claude_session'>('notification')
  const [cadence, setCadence] = useState<'recurring' | 'one_time'>('recurring')
  const [topic, setTopic] = useState('')
  const [message, setMessage] = useState('')
  const [cronExpr, setCronExpr] = useState('')
  const [whenLocal, setWhenLocal] = useState('')
  const [channel, setChannel] = useState('')
  const [target, setTarget] = useState('')
  const [spawn, setSpawn] = useState<SpawnConfigState>({
    envId: null, repoUrl: '', allowedTools: '', enablePlaywright: false, runId: '', customEnvText: '',
  })

  const { listEnvironments } = useClaude()
  const [environments, setEnvironments] = useState<Environment[]>([])
  useEffect(() => {
    if (taskType !== 'claude_session') return
    listEnvironments().then(setEnvironments).catch(() => {})
  }, [taskType, listEnvironments])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const body: CreateTaskBody = { task_type: taskType }
    if (topic) body.topic = topic
    if (message) body.message = message
    if (cadence === 'recurring') {
      body.cron_expression = cronExpr
    } else {
      // datetime-local is local time; convert to UTC ISO (with Z).
      body.scheduled_time = whenLocal ? new Date(whenLocal).toISOString() : ''
    }
    if (taskType === 'notification') {
      body.notification_channel = channel
      body.notification_target = target
    } else {
      body.spawn_config = buildSpawnConfig(spawn)
    }
    await onCreate(body)
  }

  return (
    <Modal title="New scheduled task" onClose={onCancel} size="xl">
      <form onSubmit={handleSubmit} className="space-y-4">
        <ToggleGroup label="Task type" value={taskType} onChange={setTaskType}
          options={[{ value: 'notification', label: 'Notification' }, { value: 'claude_session', label: 'Claude session' }]} />
        <Field label="Topic">
          <Input type="text" value={topic} onChange={e => setTopic(e.target.value)} />
        </Field>
        <ToggleGroup label="Cadence" value={cadence} onChange={setCadence}
          options={[{ value: 'recurring', label: 'Recurring' }, { value: 'one_time', label: 'One-time' }]} />
        {cadence === 'recurring' ? (
          <Field label="Cron expression" hint={describeCron(cronExpr)}>
            <Input type="text" value={cronExpr} onChange={e => setCronExpr(e.target.value)} placeholder="0 9 * * *" className="font-mono" />
          </Field>
        ) : (
          <Field label="Run at (local time)">
            <Input type="datetime-local" value={whenLocal} onChange={e => setWhenLocal(e.target.value)} />
          </Field>
        )}
        <Field label={taskType === 'claude_session' ? 'Initial prompt' : 'Message'}>
          <Textarea value={message} onChange={e => setMessage(e.target.value)} rows={taskType === 'claude_session' ? 4 : 2} />
        </Field>
        {taskType === 'notification'
          ? <NotificationFields channel={channel} target={target} onChannel={setChannel} onTarget={setTarget} />
          : <SpawnConfigFields state={spawn} setState={setSpawn} environments={environments} />}
        {error && <Notice tone="error">{error}</Notice>}
        <FormActions>
          <Button type="button" onClick={onCancel} disabled={saving}>Cancel</Button>
          <Button variant="primary" type="submit" disabled={saving}>{saving ? 'Creating...' : 'Create task'}</Button>
        </FormActions>
      </form>
    </Modal>
  )
}

// --- Execution History ---

interface ExecutionHistoryProps {
  taskId: string
  getExecutions: (taskId: string) => Promise<TaskExecution[]>
}

const ExecutionHistory = ({ taskId, getExecutions }: ExecutionHistoryProps) => {
  const [executions, setExecutions] = useState<TaskExecution[] | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    getExecutions(taskId)
      .then(data => { if (!cancelled) setExecutions(data) })
      .catch(() => { if (!cancelled) setExecutions([]) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [taskId, getExecutions])

  if (loading) return <Hint>Loading history...</Hint>
  if (!executions || executions.length === 0) return <Hint>No execution history</Hint>

  return (
    <List flush className="border-t border-slate-100">
      {executions.map(ex => {
        const sessionId = (ex.data as Record<string, unknown> | null)?.session_id as string | undefined
        const runId = (ex.data as Record<string, unknown> | null)?.run_id as string | undefined
        return (
          <Row
            key={ex.id}
            className="px-0"
            title={formatRelativeTime(ex.scheduled_time)}
            badges={<Badge tone={EXECUTION_STATUS_TONES[ex.status] ?? 'neutral'}>{ex.status}</Badge>}
            meta={[
              formatDuration(ex.started_at, ex.finished_at),
              sessionId && <span className="font-mono" title={sessionId}>session: {sessionId.length > 20 ? `${sessionId.slice(0, 20)}...` : sessionId}</span>,
              runId && <Link to={`/ui/agents/runs/${runId}`} className="text-primary">view run</Link>,
            ]}
          >
            {ex.error_message && <RowError>{ex.error_message}</RowError>}
            {ex.response && <p className="text-xs text-slate-600 line-clamp-2" title={ex.response}>{ex.response}</p>}
          </Row>
        )
      })}
    </List>
  )
}

// --- Task Row ---

interface TaskRowProps {
  task: ScheduledTask
  onToggle: (taskId: string, enabled: boolean) => void
  onDelete: (taskId: string) => Promise<unknown>
  onUpdate: (taskId: string, updates: UpdateTaskBody) => Promise<ScheduledTask>
  getExecutions: (taskId: string) => Promise<TaskExecution[]>
}

const TaskRow = ({ task, onToggle, onDelete, onUpdate, getExecutions }: TaskRowProps) => {
  const [editing, setEditing] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)

  const handleSave = async (updates: UpdateTaskBody) => {
    setSaving(true)
    setEditError(null)
    try {
      await onUpdate(task.id, updates)
      setEditing(false)
    } catch (e) {
      setEditError(e instanceof Error ? e.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  const typeLabel = TYPE_LABELS[task.task_type] ?? 'Notification'

  return (
    <Row
      title={task.topic || 'Untitled'}
      badges={<>
        <Badge tone="primary">{typeLabel}</Badge>
        {getTaskStatus(task) === 'done' ? (
          <Badge tone="info">Done</Badge>
        ) : (
          <StatusBadge active={task.enabled} />
        )}
      </>}
      meta={[
        describeCron(task.cron_expression),
        `Next: ${formatFutureTime(task.next_scheduled_time)}`,
        `Created: ${formatRelativeTime(task.created_at)}`,
        task.notification_channel && `${task.notification_channel}${task.notification_target ? `: ${task.notification_target}` : ''}`,
      ]}
      actions={<>
        {getTaskStatus(task) !== 'done' && (
          // Agent schedules resume on the agent's page, which re-checks their tools and scopes.
          task.task_type === 'agent_run' && !task.enabled
            ? <ButtonLink size="sm" to={`/ui/agents/${task.data?.definition_id}`}>Resume on agent page</ButtonLink>
            : <PauseButton active={task.enabled} onClick={() => onToggle(task.id, !task.enabled)} />
        )}
        {task.task_type !== 'agent_run' && <Button size="sm" onClick={() => setEditing(true)}>Edit</Button>}
        <Button size="sm" variant={showHistory ? 'primary' : 'secondary'} aria-pressed={showHistory} onClick={() => setShowHistory(h => !h)}>
          History
        </Button>
        <ConfirmButton confirm={`Delete "${task.topic || 'Untitled'}"?`} onConfirm={() => onDelete(task.id)} />
      </>}
    >
      {task.message && <p className="text-slate-600 line-clamp-2">{task.message}</p>}
      <TaskDataFields task={task} />
      {showHistory && <ExecutionHistory taskId={task.id} getExecutions={getExecutions} />}
      {editing && (
        <Modal title={`Edit ${typeLabel.toLowerCase()}`} onClose={() => { setEditing(false); setEditError(null) }} size="xl">
          <EditForm
            task={task}
            onSave={handleSave}
            onCancel={() => { setEditing(false); setEditError(null) }}
            saving={saving}
            error={editError}
          />
        </Modal>
      )}
    </Row>
  )
}

// --- Main Page ---

const EMPTY_MESSAGES: Record<TypeFilter, string> = {
  all: 'No scheduled tasks',
  notification: 'No notification tasks',
  claude_session: 'No Claude session tasks',
  agent_run: 'No agent run tasks',
}

const ScheduledTasks = () => {
  const { listTasks, toggleTask, deleteTask, updateTask, getExecutions, createTask } = useScheduledTasks()
  const [tasks, setTasks] = useState<ScheduledTask[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all')
  const { run, notice } = useActionNotice()

  const loadTasks = useCallback(async () => {
    setLoading(true)
    try {
      const filters = typeFilter !== 'all' ? { task_type: typeFilter } : {}
      const data = await listTasks(filters)
      setTasks(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load tasks')
    } finally {
      setLoading(false)
    }
  }, [listTasks, typeFilter])

  useEffect(() => { loadTasks() }, [loadTasks])

  const handleToggle = async (taskId: string, enabled: boolean) => {
    // Optimistic update
    setTasks(prev => prev.map(t => t.id === taskId ? { ...t, enabled } : t))
    const ok = await run(async () => {
      const updated = await toggleTask(taskId, enabled)
      setTasks(prev => prev.map(t => t.id === taskId ? updated : t))
    })
    if (!ok) setTasks(prev => prev.map(t => t.id === taskId ? { ...t, enabled: !enabled } : t))
  }

  const handleDelete = (taskId: string) => run(async () => {
    await deleteTask(taskId)
    setTasks(prev => prev.filter(t => t.id !== taskId))
  })

  const handleUpdate = async (taskId: string, updates: UpdateTaskBody) => {
    const updated = await updateTask(taskId, updates)
    setTasks(prev => prev.map(t => t.id === taskId ? updated : t))
    return updated
  }

  const [showCreate, setShowCreate] = useState(false)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const handleCreate = async (body: CreateTaskBody) => {
    setCreating(true)
    setCreateError(null)
    try {
      await createTask(body)
      setShowCreate(false)
      await loadTasks()
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : 'Failed to create task')
    } finally {
      setCreating(false)
    }
  }

  const enabledCount = tasks.filter(t => t.enabled).length
  const disabledCount = tasks.length - enabledCount

  const FILTERS: { value: TypeFilter; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'notification', label: 'Notifications' },
    { value: 'claude_session', label: 'Claude sessions' },
    { value: 'agent_run', label: 'Agent runs' },
  ]

  return (
    <Page
      title="Scheduled tasks"
      description={<span className="flex flex-wrap gap-x-3">
        <span>{enabledCount} active</span>
        {disabledCount > 0 && <span>{disabledCount} disabled</span>}
      </span>}
      actions={<Button variant="primary" onClick={() => setShowCreate(true)}>New task</Button>}
      width="wide"
    >
      {showCreate && <CreateForm onCreate={handleCreate} onCancel={() => setShowCreate(false)} saving={creating} error={createError} />}

      <Toolbar actions={<RefreshButton onClick={() => loadTasks()} label="Refresh task list" />}>
        <ToggleGroup label="Type" options={FILTERS} value={typeFilter} onChange={setTypeFilter} />
      </Toolbar>

      {error && <Notice tone="error" onRetry={() => loadTasks()}>{error}</Notice>}
      {notice}

      {loading ? <LoadingState message="Loading scheduled tasks..." />
        : tasks.length === 0 ? <EmptyState message={EMPTY_MESSAGES[typeFilter]} />
        : (
          <List>
            {tasks.map(task => (
              <TaskRow key={task.id} task={task} onToggle={handleToggle} onDelete={handleDelete} onUpdate={handleUpdate} getExecutions={getExecutions} />
            ))}
          </List>
        )}
    </Page>
  )
}

export default ScheduledTasks

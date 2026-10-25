import { useState, useEffect, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import { useScheduledTasks, ScheduledTask, TaskExecution, UpdateTaskBody, CreateTaskBody } from '@/hooks/useScheduledTasks'
import { useClaude, Environment } from '@/hooks/useClaude'
import { useDiscord } from '@/hooks/useDiscord'
import { useSlack } from '@/hooks/useSlack'
import { usePeople } from '@/hooks/usePeople'
import { useAuth } from '@/hooks/useAuth'
import { formatDateTimeInTimezone } from '@/utils/timezones'
import { PauseButton, StatusBadge, formatRelativeTime } from '@/components/sources/shared'
import { Badge, Button, ButtonLink, Checkbox, Combobox, ConfirmButton, type ComboOption, EmptyState, Field, FieldRow, Fieldset, FormActions, Input, List, LoadingState, Modal, Notice, Page, RefreshButton, Row, RowError, Select, SubList, ExpandToggle, Textarea, ToggleGroup, Toolbar, useActionNotice, type Tone } from '@/components/shared/ui'

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

function describeCron(expr: string | null, timezone: string): string {
  if (!expr) return 'No schedule'
  const parts = expr.trim().split(/\s+/)
  if (parts.length !== 5) return expr

  const [minute, hour, dom, month, dow] = parts

  if (dom === '*' && month === '*') {
    const timeStr = hour !== '*' && minute !== '*'
      ? `at ${hour.padStart(2, '0')}:${minute.padStart(2, '0')} ${timezone}`
      : hour !== '*'
        ? `at ${hour.padStart(2, '0')}:00 ${timezone}`
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

function formatFutureTime(dateStr: string | null, timezone: string): string {
  if (!dateStr) return 'Not scheduled'
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = date.getTime() - now.getTime()
  if (diffMs < 0) return formatRelativeTime(dateStr)

  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)
  const at = formatDateTimeInTimezone(date, timezone, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

  if (diffMins < 1) return 'any moment'
  if (diffMins < 60) return `in ${diffMins}m`
  if (diffHours < 24) return `in ${diffHours}h (${at})`
  if (diffDays < 7) return `in ${diffDays}d (${at})`
  return at
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

/** Short facts from a task's data, for the row's meta line. */
function dataFacts(task: ScheduledTask): string[] {
  const data = task.data ?? {}
  if (task.task_type === 'agent_run') return []
  if (task.task_type === 'claude_session') {
    const config = (data.spawn_config ?? {}) as Record<string, unknown>
    const tools = config.allowed_tools as string[] | undefined
    return [
      config.environment_id && `env: ${config.environment_id}`,
      config.snapshot_id && `snapshot: ${config.snapshot_id}`,
      config.repo_url && `repo: ${String(config.repo_url).replace(/^https?:\/\/github\.com\//, '')}`,
      config.run_id && `run: ${config.run_id}`,
      config.enable_playwright && 'Playwright',
      config.use_saved_write_token && 'saved write token',
      tools && `tools: ${tools.length > 3 ? `${tools.slice(0, 3).join(', ')}...` : tools.join(', ')}`,
    ].filter((f): f is string => !!f)
  }
  return [
    data.subject && `subject: ${data.subject}`,
    data.from_address && `from: ${data.from_address}`,
    data.discord_bot_id && `bot: ${data.discord_bot_id}`,
  ].filter((f): f is string => !!f)
}

const TaskDataBody = ({ task }: { task: ScheduledTask }) => {
  const data = task.data
  if (task.task_type === 'agent_run' && data) {
    return (
      <div className="text-sm text-slate-600">
        <p className="line-clamp-3">{String(data.input ?? '')}</p>
        <Link to={`/ui/agents/${data.definition_id}`} className="text-primary text-xs">Edit on the agent's page</Link>
      </div>
    )
  }
  const prompt = (data?.spawn_config as Record<string, unknown> | undefined)?.initial_prompt as string | undefined
  if (task.task_type !== 'claude_session' || !prompt || prompt.length <= 100) return null
  return <p className="text-sm text-slate-600 line-clamp-3" title={prompt}>{prompt}</p>
}

// --- Edit Form ---

interface EditFormProps {
  task: ScheduledTask
  timezone: string
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
  useSavedWriteToken: boolean
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
  if (s.useSavedWriteToken) cfg.use_saved_write_token = true
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
  <Fieldset legend="Spawn config" className="space-y-4 border-t border-slate-200 pt-4">
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
    <Checkbox label="Use my saved write token" checked={state.useSavedWriteToken} onChange={e => setState(s => ({ ...s, useSavedWriteToken: e.target.checked }))} />
  </Fieldset>
)

const envText = (env: unknown) =>
  env ? Object.entries(env as Record<string, string>).map(([k, v]) => `${k}=${v}`).join('\n') : ''

const spawnStateOf = (config: Record<string, unknown>): SpawnConfigState => ({
  envId: (config.environment_id as number) ?? null,
  repoUrl: (config.repo_url as string) || '',
  allowedTools: Array.isArray(config.allowed_tools) ? (config.allowed_tools as string[]).join(', ') : '',
  enablePlaywright: !!config.enable_playwright,
  useSavedWriteToken: !!config.use_saved_write_token,
  runId: (config.run_id as string) || '',
  customEnvText: envText(config.custom_env),
})

const EditForm = ({ task, timezone, onSave, onCancel, saving, error }: EditFormProps) => {
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
      if (spawn.useSavedWriteToken !== original.useSavedWriteToken) configUpdates.use_saved_write_token = spawn.useSavedWriteToken || null
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
      <Field label="Cron expression" hint={describeCron(cronExpr, timezone)}>
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
  timezone: string
  onCreate: (body: CreateTaskBody) => Promise<void>
  onCancel: () => void
  saving: boolean
  error: string | null
}

const CreateForm = ({ timezone, onCreate, onCancel, saving, error }: CreateFormProps) => {
  const [taskType, setTaskType] = useState<'notification' | 'claude_session'>('notification')
  const [cadence, setCadence] = useState<'recurring' | 'one_time'>('recurring')
  const [topic, setTopic] = useState('')
  const [message, setMessage] = useState('')
  const [cronExpr, setCronExpr] = useState('')
  const [whenLocal, setWhenLocal] = useState('')
  const [channel, setChannel] = useState('')
  const [target, setTarget] = useState('')
  const [spawn, setSpawn] = useState<SpawnConfigState>({
    envId: null, repoUrl: '', allowedTools: '', enablePlaywright: false, useSavedWriteToken: false, runId: '', customEnvText: '',
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
      // Sent without an offset, so the server reads it on the user's clock.
      body.scheduled_time = whenLocal
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
          <Field label="Cron expression" hint={describeCron(cronExpr, timezone)}>
            <Input type="text" value={cronExpr} onChange={e => setCronExpr(e.target.value)} placeholder="0 9 * * *" className="font-mono" />
          </Field>
        ) : (
          <Field label={`Run at (${timezone})`}>
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
  const [expanded, setExpanded] = useState(false)
  const [executions, setExecutions] = useState<TaskExecution[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!expanded) return
    let cancelled = false
    setError(null)
    getExecutions(taskId)
      .then(data => { if (!cancelled) setExecutions(data) })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load history') })
    return () => { cancelled = true }
  }, [expanded, taskId, getExecutions])

  return (
    <SubList
      title={<ExpandToggle expanded={expanded} onClick={() => setExpanded(e => !e)}>History</ExpandToggle>}
      empty={expanded && !error && (executions ? 'No execution history' : 'Loading history...')}
      footer={expanded && error && <RowError>{error}</RowError>}
    >
      {expanded && !error && executions?.map(ex => {
        const sessionId = (ex.data as Record<string, unknown> | null)?.session_id as string | undefined
        const runId = (ex.data as Record<string, unknown> | null)?.run_id as string | undefined
        return (
          <Row
            key={ex.id}
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
    </SubList>
  )
}

// --- Task Row ---

interface TaskRowProps {
  task: ScheduledTask
  timezone: string
  onToggle: (taskId: string, enabled: boolean) => void
  onDelete: (taskId: string) => Promise<unknown>
  onUpdate: (taskId: string, updates: UpdateTaskBody) => Promise<ScheduledTask>
  getExecutions: (taskId: string) => Promise<TaskExecution[]>
}

const TaskRow = ({ task, timezone, onToggle, onDelete, onUpdate, getExecutions }: TaskRowProps) => {
  const [editing, setEditing] = useState(false)
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
        describeCron(task.cron_expression, timezone),
        `Next: ${formatFutureTime(task.next_scheduled_time, timezone)}`,
        `Created: ${formatRelativeTime(task.created_at)}`,
        task.notification_channel && `${task.notification_channel}${task.notification_target ? `: ${task.notification_target}` : ''}`,
        ...dataFacts(task),
      ]}
      actions={<>
        {getTaskStatus(task) !== 'done' && (
          // Agent schedules resume on the agent's page, which re-checks their tools and scopes.
          task.task_type === 'agent_run' && !task.enabled
            ? <ButtonLink size="sm" to={`/ui/agents/${task.data?.definition_id}`}>Resume on agent page</ButtonLink>
            : <PauseButton active={task.enabled} onClick={() => onToggle(task.id, !task.enabled)} />
        )}
        {task.task_type !== 'agent_run' && <Button size="sm" onClick={() => setEditing(true)}>Edit</Button>}
        <ConfirmButton confirm={`Delete "${task.topic || 'Untitled'}"?`} onConfirm={() => onDelete(task.id)} />
      </>}
    >
      {task.message && <p className="text-slate-600 line-clamp-2">{task.message}</p>}
      <TaskDataBody task={task} />
      <ExecutionHistory taskId={task.id} getExecutions={getExecutions} />
      {editing && (
        <Modal title={`Edit ${typeLabel.toLowerCase()}`} onClose={() => { setEditing(false); setEditError(null) }} size="xl">
          <EditForm
            task={task}
            timezone={timezone}
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
  const [tasks, setTasks] = useState<ScheduledTask[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all')
  const { run, notice } = useActionNotice()
  const timezone = useAuth().user?.timezone ?? 'UTC'

  const loadTasks = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const filters = typeFilter !== 'all' ? { task_type: typeFilter } : {}
      setTasks(await listTasks(filters))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load tasks')
    } finally {
      setLoading(false)
    }
  }, [listTasks, typeFilter])

  useEffect(() => { loadTasks() }, [loadTasks])

  const handleToggle = async (taskId: string, enabled: boolean) => {
    // Optimistic update
    setTasks(prev => prev && prev.map(t => t.id === taskId ? { ...t, enabled } : t))
    const ok = await run(async () => {
      const updated = await toggleTask(taskId, enabled)
      setTasks(prev => prev && prev.map(t => t.id === taskId ? updated : t))
    })
    if (!ok) setTasks(prev => prev && prev.map(t => t.id === taskId ? { ...t, enabled: !enabled } : t))
  }

  const handleDelete = (taskId: string) => run(async () => {
    await deleteTask(taskId)
    setTasks(prev => prev && prev.filter(t => t.id !== taskId))
  })

  const handleUpdate = async (taskId: string, updates: UpdateTaskBody) => {
    const updated = await updateTask(taskId, updates)
    setTasks(prev => prev && prev.map(t => t.id === taskId ? updated : t))
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

  const shown = tasks ?? []
  const countOf = (status: TaskStatus) => shown.filter(t => getTaskStatus(t) === status).length
  const counts = [`${countOf('active')} active`, countOf('inactive') > 0 && `${countOf('inactive')} paused`, countOf('done') > 0 && `${countOf('done')} done`]

  const FILTERS: { value: TypeFilter; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'notification', label: 'Notifications' },
    { value: 'claude_session', label: 'Claude sessions' },
    { value: 'agent_run', label: 'Agent runs' },
  ]

  return (
    <Page
      title="Scheduled tasks"
      description={<span className="flex flex-wrap gap-x-3">{counts.filter(Boolean).map(c => <span key={c as string}>{c}</span>)}</span>}
      actions={<Button variant="primary" onClick={() => setShowCreate(true)}>New task</Button>}
      width="wide"
      loading={tasks === null && 'Loading scheduled tasks...'}
      error={tasks === null ? error : null}
      onRetry={loadTasks}
    >
      {showCreate && <CreateForm timezone={timezone} onCreate={handleCreate} onCancel={() => setShowCreate(false)} saving={creating} error={createError} />}

      <Toolbar actions={<RefreshButton onClick={() => loadTasks()} label="Refresh task list" />}>
        <ToggleGroup label="Type" options={FILTERS} value={typeFilter} onChange={setTypeFilter} />
      </Toolbar>

      {tasks && error && <Notice tone="error" onRetry={() => loadTasks()}>{error}</Notice>}
      {notice}

      {loading ? <LoadingState message="Loading scheduled tasks..." />
        : shown.length === 0 ? <EmptyState message={EMPTY_MESSAGES[typeFilter]} />
        : (
          <List>
            {shown.map(task => (
              <TaskRow key={task.id} task={task} timezone={timezone} onToggle={handleToggle} onDelete={handleDelete} onUpdate={handleUpdate} getExecutions={getExecutions} />
            ))}
          </List>
        )}
    </Page>
  )
}

export default ScheduledTasks

import { useState, useEffect, useCallback, useId, useRef } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useClaude, ClaudeSession, Snapshot, Environment, GithubRepoBasic, AttachInfo, ScheduleResponse, PaneInfo, ContainerStats, getLogStreamUrl, getDifferUrl } from '../../hooks/useClaude'
import { useAuth } from '../../hooks/useAuth'
import { useUsers, type User } from '../../hooks/useUsers'
import XtermTerminal from './XtermTerminal'
import GithubTokens from './GithubTokens'
import SSHKeySelect from './SSHKeySelect'
import ClaudeFleetStats, { SessionStatsPanel, Usage, formatMb, sessionDisplay } from './ClaudeFleetStats'
import { Badge, Button, Checkbox, ConfirmButton, DetailList, Disclosure, Field, Fieldset, Input, List, Notice, Page, PaneHeader, Row, Select, SplitView, Textarea, ToggleGroup, type Tone } from '@/components/shared/ui'
import { formatUserDateTime } from '@/utils/timezones'

const COMMON_TOOLS = [
  'Bash', 'Edit', 'Write', 'Read', 'Glob', 'Grep',
  'Task', 'WebFetch', 'WebSearch', 'NotebookEdit',
  'TodoWrite', 'AskUserQuestion', "MCPSearch"
]
const ALLOWED_TOOLS_STORAGE_KEY = 'claude_session_allowed_tools'
const CUSTOM_ENV_STORAGE_KEY = 'claude_session_custom_env'

interface ScreenMessage {
  type: 'screen' | 'error' | 'status' | 'panes'
  data: string
  timestamp: string
  cols?: number
  rows?: number
  scrolled?: number
  pane_count?: number
  pane?: string
  command?: string
  panes?: PaneInfo[]
  stats?: ContainerStats | null
}

const KILL_CONFIRM = 'Kill this session? The container will be stopped and removed.'

const STATUS_TONES: Record<string, Tone> = {
  running: 'success',
  created: 'info',
  exited: 'neutral',
  paused: 'warning',
}

const CRON_PRESETS = [
  { label: 'Daily 09:00 UTC', value: '0 9 * * *' },
  { label: 'Weekdays 09:00 UTC', value: '0 9 * * 1-5' },
  { label: 'Mondays 09:00 UTC', value: '0 9 * * 1' },
]

const ContainerStatsBadge: React.FC<{ stats: ContainerStats }> = ({ stats }) => {
  const { memory, cpu } = stats
  // CPU pct is reported relative to a single core, so a 200% limit means 2 cores.
  // Show "12% / 200%" so the limit is visible.
  const cpuPct = Math.round(cpu.pct)
  const cpuPctOfLimit = cpu.limit_pct > 0 ? (cpu.pct / cpu.limit_pct) * 100 : 0
  return (
    <div className="flex items-center gap-3 text-xs text-slate-500">
      <span className="flex items-center gap-1" title={`CPU: ${cpu.pct.toFixed(1)}% of ${cpu.limit_pct}% limit`}>
        <span className="font-medium text-slate-600">CPU</span>
        <Usage pct={cpuPctOfLimit}>{cpuPct}% / {cpu.limit_pct}%</Usage>
      </span>
      <span className="flex items-center gap-1" title={`Memory: ${memory.used_mb} MB of ${memory.limit_mb} MB`}>
        <span className="font-medium text-slate-600">Mem</span>
        <Usage pct={memory.pct}>{formatMb(memory.used_mb)} / {formatMb(memory.limit_mb)} ({Math.round(memory.pct)}%)</Usage>
      </span>
    </div>
  )
}

const ClaudeSessions = () => {
  const {
    listSessions,
    killSession,
    spawnSession,
    scheduleSession,
    getAttachInfo,
    getOrchestratorStatus,
    listSnapshots,
    listEnvironments,
    listUserRepos,
  } = useClaude()
  const { hasScope, user: authUser } = useAuth()
  const { listUsers } = useUsers()
  const navigate = useNavigate()
  const { sessionId: urlSessionId } = useParams<{ sessionId?: string }>()

  // State
  const [sessions, setSessions] = useState<ClaudeSession[]>([])
  const [selectedSession, setSelectedSession] = useState<ClaudeSession | null>(null)
  const [users, setUsers] = useState<User[]>([])
  const [attachInfo, setAttachInfo] = useState<AttachInfo | null>(null)
  const [attachError, setAttachError] = useState<string | null>(null)
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [environments, setEnvironments] = useState<Environment[]>([])
  const [repos, setRepos] = useState<GithubRepoBasic[]>([])
  const [orchestratorAvailable, setOrchestratorAvailable] = useState<boolean | null>(null)

  // Screen streaming state
  const [screenContent, setScreenContent] = useState<string>('')
  const [scrollOffset, setScrollOffset] = useState(0)
  const [wsConnected, setWsConnected] = useState(false)
  const [wsError, setWsError] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)

  // Pane state
  const [paneCount, setPaneCount] = useState(1)
  const [activePane, setActivePane] = useState<string>('')
  const [panes, setPanes] = useState<PaneInfo[]>([])
  const [containerStats, setContainerStats] = useState<ContainerStats | null>(null)

  // UI State
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [sessionsError, setSessionsError] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [showNewSession, setShowNewSession] = useState(false)
  const [killingId, setKillingId] = useState<string | null>(null)
  const [spawning, setSpawning] = useState(false)

  // New session form - config source can be a snapshot or an environment
  type ConfigSelection = { type: 'snapshot'; id: number } | { type: 'environment'; id: number } | null
  const [selectedConfig, setSelectedConfig] = useState<ConfigSelection>(null)
  const [selectedRepoUrl, setSelectedRepoUrl] = useState<string>('')
  const [enablePlaywright, setEnablePlaywright] = useState<boolean>(false)
  const [devChannelsServer, setDevChannelsServer] = useState<string>('')
  const [allowedTools, setAllowedTools] = useState<string[]>(() => {
    try {
      const saved = localStorage.getItem(ALLOWED_TOOLS_STORAGE_KEY)
      return saved ? JSON.parse(saved) : COMMON_TOOLS
    } catch {
      return COMMON_TOOLS
    }
  })
  const [customEnvText, setCustomEnvText] = useState<string>(() => {
    return localStorage.getItem(CUSTOM_ENV_STORAGE_KEY) || ''
  })
  const [initialPrompt, setInitialPrompt] = useState<string>('')
  const [promptError, setPromptError] = useState<string | null>(null)
  const promptRef = useRef<HTMLTextAreaElement>(null)
  const [runId, setRunId] = useState<string>('')
  const [cronExpression, setCronExpression] = useState<string>('')
  const [scheduling, setScheduling] = useState(false)
  const [scheduleSuccess, setScheduleSuccess] = useState<ScheduleResponse | null>(null)

  // Selecting a session goes through the URL so refresh preserves the
  // choice. The state setter still exists for the load-time path (sync from
  // URL once the session list arrives) — UI callsites use selectSession.
  const selectSession = useCallback(
    (s: ClaudeSession | null) => {
      navigate(s ? `/ui/claude/${s.session_id}` : '/ui/claude')
    },
    [navigate]
  )

  // Read through a ref so a selection change doesn't recreate loadSessions and refetch the list.
  const selectedSessionRef = useRef(selectedSession)
  selectedSessionRef.current = selectedSession

  // Load data
  const loadSessions = useCallback(async () => {
    try {
      const data = await listSessions()
      setSessions(data)
      setSessionsError(null)
      setOrchestratorAvailable(true)
      // If selected session no longer exists, clear it (and the URL).
      const selected = selectedSessionRef.current
      if (selected && !data.find((s) => s.session_id === selected.session_id)) {
        setSelectedSession(null)
        setAttachInfo(null)
        navigate('/ui/claude')
      }
    } catch (e) {
      setSessionsError(e instanceof Error ? e.message : 'Failed to list sessions')
      // A failing list usually means the orchestrator went away; the status call says so without internals.
      getOrchestratorStatus().then(status => setOrchestratorAvailable(status.available)).catch(() => {})
    }
  }, [listSessions, getOrchestratorStatus, navigate])

  const loadInitialData = useCallback(async () => {
    setLoading(true)
    try {
      const [snapshotsData, environmentsData, reposData, status] = await Promise.all([
        listSnapshots(),
        listEnvironments(),
        listUserRepos(),
        getOrchestratorStatus(),
      ])
      setSnapshots(snapshotsData)
      setEnvironments(environmentsData)
      setRepos(reposData)
      setOrchestratorAvailable(status.available)
      // Default to first environment if available, otherwise first snapshot
      if (environmentsData.length > 0) {
        setSelectedConfig({ type: 'environment', id: environmentsData[0].id })
      } else if (snapshotsData.length > 0) {
        setSelectedConfig({ type: 'snapshot', id: snapshotsData[0].id })
      }
      setLoadError(null)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [listSnapshots, listEnvironments, listUserRepos, getOrchestratorStatus])

  useEffect(() => {
    loadInitialData()
  }, [loadInitialData])

  // Sessions load apart from the static data, so an offline orchestrator leaves the rest of the page usable.
  useEffect(() => {
    loadSessions()
    const interval = setInterval(loadSessions, 5000)
    return () => clearInterval(interval)
  }, [loadSessions])

  // Keep selectedSession in sync with the URL. Runs on mount and whenever
  // either the URL or the session list changes — the list might arrive after
  // the URL, so we re-resolve as soon as the matching session shows up.
  useEffect(() => {
    if (urlSessionId) {
      const match = sessions.find((s) => s.session_id === urlSessionId)
      if (match) {
        if (match.session_id !== selectedSession?.session_id) {
          setSelectedSession(match)
        }
      } else if (sessions.length > 0 && selectedSession) {
        // URL points at something the list says doesn't exist (kicked, killed,
        // or never existed) — clear local selection but leave the URL alone
        // so the user can correct it manually if they typed it.
        setSelectedSession(null)
      }
    } else if (selectedSession) {
      setSelectedSession(null)
    }
  }, [urlSessionId, sessions, selectedSession])

  // Load users for the fleet table's "user" column. Only admins can list
  // users; non-admins skip the call (they only see their own sessions
  // anyway, so the lookup is moot).
  useEffect(() => {
    if (!hasScope('*')) return
    listUsers()
      .then(setUsers)
      .catch(() => {
        // Non-fatal: rows fall back to "user N".
      })
  }, [hasScope, listUsers])

  // Load attach info when session selected
  useEffect(() => {
    if (!selectedSession) {
      setAttachInfo(null)
      return
    }
    setAttachError(null)
    getAttachInfo(selectedSession.session_id)
      .then(setAttachInfo)
      .catch(e => {
        setAttachInfo(null)
        setAttachError(e instanceof Error ? e.message : 'Failed to get attach info')
      })
  }, [selectedSession, getAttachInfo])

  // WebSocket connection for real-time screen streaming
  useEffect(() => {
    if (!selectedSession) {
      setScreenContent('')
      setWsConnected(false)
      setWsError(null)
      return
    }

    const wsUrl = getLogStreamUrl(selectedSession.session_id)
    if (!wsUrl) {
      setWsError('No authentication token available')
      return
    }

    // Close existing connection
    if (wsRef.current) {
      wsRef.current.close()
    }

    setScreenContent('')
    setWsError(null)
    setPaneCount(1)
    setActivePane('')
    setPanes([])
    setContainerStats(null)

    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      setWsConnected(true)
      setWsError(null)
    }

    ws.onmessage = (event) => {
      try {
        const msg: ScreenMessage = JSON.parse(event.data)
        if (msg.type === 'screen') {
          setScreenContent(msg.data)
          setScrollOffset(msg.scrolled || 0)
          // TCP relay includes pane_count with every capture
          if (msg.pane_count !== undefined) setPaneCount(msg.pane_count)
          if (msg.pane !== undefined) setActivePane(msg.pane)
        } else if (msg.type === 'panes') {
          // Pane list update from the pane polling loop
          setPaneCount(msg.pane_count || 1)
          if (msg.panes) {
            // Sort by stable tmux pane ID (0, 1, 3, ...) for consistent chip order
            const sorted = [...msg.panes].sort((a, b) => {
              const numA = parseInt(a.id, 10)
              const numB = parseInt(b.id, 10)
              return numA - numB
            })
            setPanes(sorted)
            // Track which pane is active (by stable id)
            const active = sorted.find(p => p.active)
            if (active) setActivePane(active.id)
          }
          // Stats may be null if the relay doesn't provide them
          if (msg.stats !== undefined) setContainerStats(msg.stats)
        } else if (msg.type === 'error') {
          setWsError(msg.data)
        }
        // Ignore 'status', 'log', 'logs', 'phase' messages - terminal handles display
      } catch (e) {
        console.error('Failed to parse WebSocket message:', e)
      }
    }

    ws.onerror = () => {
      setWsError('WebSocket connection error')
      setWsConnected(false)
    }

    ws.onclose = (event) => {
      setWsConnected(false)
      if (event.code !== 1000) {
        setWsError(event.reason || 'Connection closed')
      }
    }

    return () => {
      ws.close()
      wsRef.current = null
    }
  }, [selectedSession])

  // Handler for switching panes via WebSocket
  const handleSelectPane = useCallback((paneId: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
    wsRef.current.send(JSON.stringify({ type: 'select_pane', pane: paneId }))
  }, [])

  // Persist allowed tools to localStorage
  useEffect(() => {
    localStorage.setItem(ALLOWED_TOOLS_STORAGE_KEY, JSON.stringify(allowedTools))
  }, [allowedTools])

  // Persist custom env to localStorage
  useEffect(() => {
    localStorage.setItem(CUSTOM_ENV_STORAGE_KEY, customEnvText)
  }, [customEnvText])

  // Parse KEY=VALUE text into Record<string, string>
  const parseEnvText = (text: string): Record<string, string> => {
    const env: Record<string, string> = {}
    for (const line of text.split('\n')) {
      const trimmed = line.trim()
      if (!trimmed || trimmed.startsWith('#')) continue
      const eqIndex = trimmed.indexOf('=')
      if (eqIndex > 0) {
        const key = trimmed.slice(0, eqIndex).trim()
        const value = trimmed.slice(eqIndex + 1).trim()
        if (key) env[key] = value
      }
    }
    return env
  }

  // Toggle tool in allowed list
  const toggleTool = (tool: string) => {
    setAllowedTools(prev =>
      prev.includes(tool)
        ? prev.filter(t => t !== tool)
        : [...prev, tool]
    )
  }

  // Handlers
  const handleSelectSession = (session: ClaudeSession) => {
    selectSession(session)
    setShowNewSession(false)
  }

  const handleKillSession = async (sessionId: string) => {
    setKillingId(sessionId)
    try {
      await killSession(sessionId)
      if (selectedSession?.session_id === sessionId) {
        selectSession(null)
        setAttachInfo(null)
      }
      await loadSessions()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to kill session')
    } finally {
      setKillingId(null)
    }
  }

  const [sshChoice, setSSHChoice] = useState('')

  const buildSpawnConfig = () => {
    const customEnv = parseEnvText(customEnvText)
    return {
      snapshot_id: selectedConfig?.type === 'snapshot' ? selectedConfig.id : undefined,
      environment_id: selectedConfig?.type === 'environment' ? selectedConfig.id : undefined,
      repo_url: selectedRepoUrl || undefined,
      ...(sshChoice === 'none' ? { use_ssh_key: false } : sshChoice ? { ssh_key_id: Number(sshChoice) } : {}),
      enable_playwright: enablePlaywright || undefined,
      allowed_tools: allowedTools.length > 0 ? allowedTools : undefined,
      custom_env: Object.keys(customEnv).length > 0 ? customEnv : undefined,
      initial_prompt: initialPrompt || undefined,
      run_id: runId || undefined,
      dev_channels_server: devChannelsServer.trim() || undefined,
    }
  }

  const handleSpawnSession = async () => {
    setFormError(null)
    setSpawning(true)
    try {
      const newSession = await spawnSession({ ...buildSpawnConfig(), use_saved_write_token: true })
      await loadSessions()
      selectSession(newSession)
      setShowNewSession(false)
      // Reset form
      setSelectedRepoUrl('')
      setInitialPrompt('')
      setRunId('')
    } catch (e) {
      setFormError(e instanceof Error ? e.message : 'Failed to spawn session')
    } finally {
      setSpawning(false)
    }
  }

  const handleScheduleSession = async () => {
    setFormError(null)
    setScheduling(true)
    setScheduleSuccess(null)
    try {
      const result = await scheduleSession({
        cron_expression: cronExpression,
        spawn_config: buildSpawnConfig(),
      })
      setScheduleSuccess(result)
      // Reset form
      setInitialPrompt('')
      setRunId('')
      setCronExpression('')
    } catch (e) {
      setFormError(e instanceof Error ? e.message : 'Failed to schedule session')
    } finally {
      setScheduling(false)
    }
  }

  const handleNewSession = () => {
    selectSession(null)
    setAttachInfo(null)
    setShowNewSession(true)
  }

  // Shown while no session is selected. Admins see global capacity and every
  // container; non-admins see only their own.
  const fleet = (
            <div className="max-w-4xl p-4 md:p-6">
              <div className="flex flex-wrap items-baseline justify-between gap-2 mb-4">
                <h2 className="text-lg font-semibold text-slate-800">Fleet overview</h2>
                <p className="text-sm text-slate-600">
                  Select a session for details, or open one from the table below.
                </p>
              </div>
              <ClaudeFleetStats
                environments={environments}
                hasAdminScope={hasScope('*')}
                currentUserId={authUser?.id ?? null}
              />
            </div>
  )

  const fleetDown = !!loadError || orchestratorAvailable === false
  const offlineId = useId()

  return (
    <Page
      title={<>Claude sessions{orchestratorAvailable === false && <Badge id={offlineId} tone="danger">Orchestrator offline</Badge>}</>}
      width="full"
      fill
      loading={loading}
      error={loadError}
      onRetry={loadInitialData}
      actions={<Button variant="primary" onClick={handleNewSession} disabled={fleetDown}
        aria-describedby={orchestratorAvailable === false ? offlineId : undefined}>New session</Button>}
    >
      <div className="h-full flex flex-col gap-4">
      {orchestratorAvailable === false
        ? <Notice tone="error">Claude sessions are unavailable: the session orchestrator isn't running.</Notice>
        : sessionsError && <Notice tone="error" onRetry={loadSessions}>Couldn't refresh sessions: {sessionsError}</Notice>}
      {error && <Notice tone="error" onDismiss={() => setError(null)}>{error}</Notice>}
      <div className="flex-1 min-h-0">
      <SplitView
        listTitle="Active sessions"
        list={sessions.length === 0 ? (
          <p className="p-4 text-sm text-slate-600 text-center">No active sessions</p>
        ) : (
          <List flush>
            {sessions.map((session) => {
              const display = sessionDisplay(session.session_id, environments, users)
              return (
              <Row
                key={session.session_id}
                title={<>{display.title}{' '}<span className="sr-only">{session.session_id}</span></>}
                meta={[display.subtitle, <span className="font-mono">{session.session_id}</span>]}
                onSelect={() => handleSelectSession(session)}
                selected={selectedSession?.session_id === session.session_id}
                badges={<Badge tone={STATUS_TONES[session.status?.toLowerCase() ?? ''] ?? 'neutral'}>{session.status || 'unknown'}</Badge>}
                actions={
                  <ConfirmButton variant="ghost" confirm={KILL_CONFIRM}
                    confirmLabel="Kill session" onConfirm={() => handleKillSession(session.session_id)}
                    disabled={killingId === session.session_id} title="Kill session" aria-label={`Kill session ${session.session_id}`}>
                    {killingId === session.session_id ? '...' : <span aria-hidden="true">&times;</span>}
                  </ConfirmButton>
                }
              />
              )
            })}
          </List>
        )}
        placeholder={orchestratorAvailable === false ? 'Fleet stats show once the orchestrator is running.' : fleet}
        onBack={() => { setShowNewSession(false); selectSession(null) }}
        backLabel="Sessions"
        detail={
          showNewSession ? (
            <>
            <PaneHeader title="Start new session" />
            <div className="flex-1 overflow-y-auto p-4 md:p-6">
            <div className="max-w-xl">

              {snapshots.length === 0 && environments.length === 0 ? (
                <Notice tone="warning">
                  <p className="font-medium">No snapshots or environments available</p>
                  <p className="text-sm mt-1">
                    Create a snapshot first using the CLI tool:
                    <code className="block bg-yellow-100 px-2 py-1 rounded mt-2 text-xs">
                      python tools/claude_snapshot.py --host $HOST --api-key $KEY
                    </code>
                  </p>
                </Notice>
              ) : (
                <form className="space-y-6" onSubmit={(e) => {
                  e.preventDefault()
                  if (!selectedConfig) return
                  if (cronExpression && !initialPrompt) {
                    setPromptError('Required for a scheduled session.')
                    promptRef.current?.focus()
                    return
                  }
                  if (cronExpression) handleScheduleSession()
                  else handleSpawnSession()
                }}>
                  {/* Config source selection (grouped: environments + snapshots) */}
                  <Field label="Configuration" hint={<>{selectedConfig?.type === 'environment' ? 'Environment: State persists across sessions' : 'Snapshot: Fresh extraction each time'}</>}>
                    <Select
                      value={selectedConfig ? `${selectedConfig.type}:${selectedConfig.id}` : ''}
                      onChange={(e) => {
                        const [type, id] = e.target.value.split(':')
                        if (type === 'environment' || type === 'snapshot') {
                          setSelectedConfig({ type, id: Number(id) })
                        }
                      }}
                    >
                      {environments.length > 0 && (
                        <optgroup label="Environments (persistent)">
                          {environments.map((e) => {
                            const activeCount = sessions.filter((s) => s.environment_id === e.id).length
                            return (
                              <option key={`env-${e.id}`} value={`environment:${e.id}`}>
                                {e.name}
                                {activeCount > 0 && ` (${activeCount} active)`}
                              </option>
                            )
                          })}
                        </optgroup>
                      )}
                      {snapshots.length > 0 && (
                        <optgroup label="Snapshots (fresh each time)">
                          {snapshots.map((s) => (
                            <option key={`snap-${s.id}`} value={`snapshot:${s.id}`}>
                              {s.name}
                              {s.subscription_type && ` (${s.subscription_type})`}
                            </option>
                          ))}
                        </optgroup>
                      )}
                    </Select>
                  </Field>

                  {/* Repo selection */}
                  <Field label="Repository (optional)" hint="The repo will be cloned into the workspace">
                    <Select
                      value={selectedRepoUrl}
                      onChange={(e) => setSelectedRepoUrl(e.target.value)}
                    >
                      <option value="">No repository</option>
                      {repos.map((r) => (
                        <option key={r.id} value={`git@github.com:${r.owner}/${r.name}.git`}>
                          {r.owner}/{r.name}
                        </option>
                      ))}
                    </Select>
                  </Field>

                  {/* Branch ID (only shown when repo selected) */}
                  {selectedRepoUrl && (
                    <Field label="Branch ID (optional)" hint={<>Git branch will be <code className="bg-slate-100 px-1 rounded">claude/{runId || '<session-id>'}</code>. Leave blank for auto-generated ID.</>}>
                      <Input
                        type="text"
                        value={runId}
                        onChange={(e) => setRunId(e.target.value)}
                        placeholder="my-feature"
                        pattern="[a-zA-Z0-9_-]+"
                        className="font-mono"
                      />
                    </Field>
                  )}

                  <GithubTokens />
                  <SSHKeySelect value={sshChoice} onChange={setSSHChoice} />

                  {/* Playwright toggle */}
                  <Checkbox checked={enablePlaywright} onChange={(e) => setEnablePlaywright(e.target.checked)}
                    label={<>Enable Playwright <span className="text-xs text-slate-500">(headless browser automation via MCP)</span></>} />

                  {/* Dev channels server */}
                  <Field label="Dev channels server (optional)" hint="Launches Claude with --dangerously-load-development-channels. Leave blank to disable.">
                    <Input
                      type="text"
                      value={devChannelsServer}
                      onChange={(e) => setDevChannelsServer(e.target.value)}
                      placeholder="http://channels.internal:3000"
                      className="font-mono"
                    />
                  </Field>

                  <Fieldset legend="Pre-approved tools" hint="These tools won't require permission prompts. MCP tools always require permission." className="space-y-2">
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                      {COMMON_TOOLS.map(tool => (
                        <Checkbox key={tool} checked={allowedTools.includes(tool)} onChange={() => toggleTool(tool)} label={tool} />
                      ))}
                    </div>
                  </Fieldset>

                  <Field label="Custom environment variables" hint="One per line: KEY=value. Lines starting with # are ignored.">
                    <Textarea
                      value={customEnvText}
                      onChange={(e) => setCustomEnvText(e.target.value)}
                      placeholder="MY_VAR=some_value&#10;ANOTHER_VAR=123"
                      rows={3}
                      className="font-mono"
                    />
                  </Field>

                  <Field label="Initial prompt" hint="Claude starts working on it straight away. Optional, except for a schedule."
                    error={promptError}>
                    <Textarea
                      ref={promptRef}
                      value={initialPrompt}
                      onChange={(e) => {
                        setInitialPrompt(e.target.value)
                        setPromptError(null)
                      }}
                      placeholder="Start Claude with this prompt..."
                      rows={3}
                    />
                  </Field>

                  <div className="space-y-2">
                    <Field label="Schedule (optional)" hint={<>Standard 5-field cron, in UTC: minute hour day-of-month month day-of-week.{cronExpression && ' This creates a recurring schedule instead of starting a session now.'}</>}>
                      <Input
                        type="text"
                        value={cronExpression}
                        onChange={(e) => setCronExpression(e.target.value)}
                        placeholder="0 9 * * *"
                        className="font-mono"
                      />
                    </Field>
                    <div className="flex flex-wrap items-center gap-2">
                      <ToggleGroup label="Schedule presets" value={cronExpression} onChange={setCronExpression} options={CRON_PRESETS} />
                      {cronExpression && <Button size="sm" type="button" onClick={() => setCronExpression('')}>Clear</Button>}
                    </div>
                  </div>

                  {/* Schedule success message */}
                  {scheduleSuccess && (
                    <Notice tone="success" onDismiss={() => setScheduleSuccess(null)}>
                      <p className="font-medium">Schedule created</p>
                      <p className="text-sm mt-1">
                        Task ID: <code className="bg-green-100 px-1 rounded">{scheduleSuccess.task_id}</code>
                      </p>
                      <p className="text-sm mt-1">
                        Next run: {formatUserDateTime(scheduleSuccess.next_scheduled_time)}
                      </p>
                    </Notice>
                  )}

                  {formError && <Notice tone="error" onDismiss={() => setFormError(null)}>{formError}</Notice>}

                  {/* Actions */}
                  <div className="flex gap-3">
                    <Button variant="primary" type="submit"
                      disabled={spawning || scheduling || !selectedConfig}
                    >
                      {spawning
                        ? 'Starting...'
                        : scheduling
                          ? 'Scheduling...'
                          : cronExpression
                            ? 'Schedule recurring session'
                            : 'Start session'}
                    </Button>
                    <Button type="button" onClick={() => setShowNewSession(false)}>
                      Cancel
                    </Button>
                  </div>
                </form>
              )}
            </div>
            </div>
            </>
          ) : selectedSession ? (
            // Selected session details
            (() => {
              const display = sessionDisplay(selectedSession.session_id, environments, users)
              return (
            <>
            <PaneHeader
              title={<>{display.title}{display.subtitle && <span className="block text-xs font-normal text-slate-500">{display.subtitle}</span>}</>}
              actions={<>
                <Badge tone={STATUS_TONES[selectedSession.status?.toLowerCase() ?? ''] ?? 'neutral'}>{selectedSession.status || 'unknown'}</Badge>
                <Button variant="ghost" size="sm" onClick={() => selectSession(null)} title="Back to fleet overview">Fleet overview</Button>
                <ConfirmButton confirm={KILL_CONFIRM} onConfirm={() => handleKillSession(selectedSession.session_id)} disabled={killingId === selectedSession.session_id}
                  confirmLabel="Kill session">
                  {killingId === selectedSession.session_id ? 'Killing...' : 'Kill session'}
                </ConfirmButton>
              </>}
            />
            <div className="flex-1 min-h-0 overflow-y-auto p-4 flex flex-col gap-4">
              <DetailList items={[
                ['Session ID', <span className="font-mono">{selectedSession.session_id}</span>],
                ['Container', <span className="font-mono">{selectedSession.container_name || 'N/A'}</span>],
              ]} />

              {/* Resource stats — collapsed by default; the chart polls only
                  while expanded (mounted) so closed panels are zero cost. */}
              <Disclosure summary="Resource stats">
                <SessionStatsPanel sessionId={selectedSession.session_id} />
              </Disclosure>

              {attachError && <Notice tone="error">Couldn't load connect commands: {attachError}</Notice>}
              {attachInfo && (
                <Disclosure summary="Connect commands">
                  <div className="space-y-3">
                    <div>
                      <div className="text-xs text-slate-500 mb-1">Attach to Claude (interactive)</div>
                      <code className="block bg-slate-900 text-green-400 px-3 py-2 rounded text-sm font-mono">
                        {attachInfo.attach_cmd}
                      </code>
                    </div>
                    <div>
                      <div className="text-xs text-slate-500 mb-1">Open shell alongside</div>
                      <code className="block bg-slate-900 text-green-400 px-3 py-2 rounded text-sm font-mono">
                        {attachInfo.exec_cmd}
                      </code>
                    </div>
                    <p className="text-xs text-slate-500">
                      Detach without killing: <kbd className="bg-slate-100 px-1 rounded">Ctrl+P</kbd> then{' '}
                      <kbd className="bg-slate-100 px-1 rounded">Ctrl+Q</kbd>
                    </p>
                  </div>
                </Disclosure>
              )}

              {/* Terminal screen */}
              <div className="flex-1 flex flex-col min-h-0">
                <div className="flex items-center mb-2 gap-3">
                  <div className="text-sm font-medium text-slate-700 flex items-center gap-2">
                    <span>Terminal</span>
                    {wsConnected ? (
                      <Badge tone="success">Connected</Badge>
                    ) : wsError ? (
                      <Badge tone="danger">Disconnected</Badge>
                    ) : (
                      <Badge tone="warning">Connecting...</Badge>
                    )}
                  </div>
                  {containerStats && <ContainerStatsBadge stats={containerStats} />}
                </div>
                {wsError && !wsConnected && <Notice tone="error" className="mb-2">{wsError}</Notice>}
                <div className="flex-1 bg-slate-900 rounded-lg overflow-hidden min-h-[400px]">
                  <XtermTerminal
                    wsRef={wsRef}
                    screenContent={screenContent}
                    scrollOffset={scrollOffset}
                    connected={wsConnected}
                  />
                </div>
                {/* Pane switcher chips */}
                {paneCount > 1 && (
                  <div className="flex items-center gap-2 mt-2 flex-wrap">
                    <span className="text-xs text-slate-500 mr-1">Panes:</span>
                    <ToggleGroup label="Panes" value={activePane} onChange={handleSelectPane}
                      options={panes.length > 0
                        ? panes.map(pane => ({ value: pane.id, label: pane.title || pane.command?.split('/').pop() || pane.id }))
                        : Array.from({ length: paneCount }, (_, i) => ({ value: `0.${i}`, label: `0.${i}` }))} />
                  </div>
                )}
              </div>

              {/* Differ code review panel */}
              {selectedSession?.differ && (
                <Disclosure summary="Code review (Differ)">
                  <div>
                    <iframe
                      src={getDifferUrl(selectedSession.session_id)}
                      className="w-full border-0 rounded-b-lg"
                      style={{ height: '70vh', minHeight: '500px' }}
                      title="Differ code review"
                      sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
                    />
                  </div>
                </Disclosure>
              )}
            </div>
            </>
              )
            })()
          ) : null
        }
      />
      </div>
      </div>
    </Page>
  )
}

export default ClaudeSessions

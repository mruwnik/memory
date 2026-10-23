import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useClaude, ClaudeSession, Snapshot, Environment, GithubRepoBasic, AttachInfo, ScheduleResponse, PaneInfo, ContainerStats, getLogStreamUrl, getDifferUrl } from '../../hooks/useClaude'
import { useAuth } from '../../hooks/useAuth'
import { useUsers, type User } from '../../hooks/useUsers'
import XtermTerminal from './XtermTerminal'
import ClaudeFleetStats, { SessionStatsPanel, sessionDisplay } from './ClaudeFleetStats'
import { Badge, Button, Input, Label, Notice, Page, Select, Textarea, ToggleGroup } from '@/components/shared/ui'

const COMMON_TOOLS = [
  'Bash', 'Edit', 'Write', 'Read', 'Glob', 'Grep',
  'Task', 'WebFetch', 'WebSearch', 'NotebookEdit',
  'TodoWrite', 'AskUserQuestion', "MCPSearch"
]
const ALLOWED_TOOLS_STORAGE_KEY = 'claude_session_allowed_tools'
const CUSTOM_ENV_STORAGE_KEY = 'claude_session_custom_env'
// SECURITY NOTE: GitHub tokens stored in localStorage are accessible to any JS on this origin.
// This is acceptable for this single-user application where:
// - The user explicitly enters their own tokens
// - No third-party scripts are loaded
// - Tokens can also reference server-side secrets by name instead of literal PATs
// For multi-user or public deployments, use server-side session storage instead.
const GITHUB_TOKEN_STORAGE_KEY = 'claude_session_github_token'
const GITHUB_TOKEN_WRITE_STORAGE_KEY = 'claude_session_github_token_write'

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

const STATUS_COLORS: Record<string, string> = {
  running: 'bg-green-100 text-green-700',
  created: 'bg-blue-100 text-blue-700',
  exited: 'bg-slate-100 text-slate-600',
  paused: 'bg-yellow-100 text-yellow-700',
}

const formatMb = (mb: number): string => {
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`
  return `${Math.round(mb)} MB`
}

const usagePctColor = (pct: number): string => {
  if (pct >= 90) return 'text-red-600'
  if (pct >= 75) return 'text-yellow-600'
  return 'text-slate-600'
}

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
        <span className={`font-mono ${usagePctColor(cpuPctOfLimit)}`}>
          {cpuPct}% / {cpu.limit_pct}%
        </span>
      </span>
      <span className="flex items-center gap-1" title={`Memory: ${memory.used_mb} MB of ${memory.limit_mb} MB`}>
        <span className="font-medium text-slate-600">Mem</span>
        <span className={`font-mono ${usagePctColor(memory.pct)}`}>
          {formatMb(memory.used_mb)} / {formatMb(memory.limit_mb)} ({Math.round(memory.pct)}%)
        </span>
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
  const [githubToken, setGithubToken] = useState<string>(() => {
    return localStorage.getItem(GITHUB_TOKEN_STORAGE_KEY) || ''
  })
  const [githubTokenWrite, setGithubTokenWrite] = useState<string>(() => {
    return localStorage.getItem(GITHUB_TOKEN_WRITE_STORAGE_KEY) || ''
  })
  const [initialPrompt, setInitialPrompt] = useState<string>('')
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

  // Load data
  const loadSessions = useCallback(async () => {
    try {
      const data = await listSessions()
      setSessions(data)
      // If selected session no longer exists, clear it (and the URL).
      if (selectedSession && !data.find((s) => s.session_id === selectedSession.session_id)) {
        setSelectedSession(null)
        setAttachInfo(null)
        navigate('/ui/claude')
      }
    } catch (e) {
      console.error('Failed to load sessions:', e)
    }
  }, [listSessions, selectedSession, navigate])

  const loadInitialData = useCallback(async () => {
    setLoading(true)
    try {
      const [sessionsData, snapshotsData, environmentsData, reposData, status] = await Promise.all([
        listSessions(),
        listSnapshots(),
        listEnvironments(),
        listUserRepos(),
        getOrchestratorStatus(),
      ])
      setSessions(sessionsData)
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
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [listSessions, listSnapshots, listEnvironments, listUserRepos, getOrchestratorStatus])

  useEffect(() => {
    loadInitialData()
  }, [loadInitialData])

  // Poll for session updates
  useEffect(() => {
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
    getAttachInfo(selectedSession.session_id)
      .then(setAttachInfo)
      .catch(() => setAttachInfo(null))
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

  // Persist github tokens to localStorage
  useEffect(() => {
    localStorage.setItem(GITHUB_TOKEN_STORAGE_KEY, githubToken)
  }, [githubToken])

  useEffect(() => {
    localStorage.setItem(GITHUB_TOKEN_WRITE_STORAGE_KEY, githubTokenWrite)
  }, [githubTokenWrite])

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
    if (!confirm('Kill this session? The container will be stopped and removed.')) return
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

  const buildSpawnConfig = () => {
    const customEnv = parseEnvText(customEnvText)
    return {
      snapshot_id: selectedConfig?.type === 'snapshot' ? selectedConfig.id : undefined,
      environment_id: selectedConfig?.type === 'environment' ? selectedConfig.id : undefined,
      repo_url: selectedRepoUrl || undefined,
      github_token: githubToken || undefined,
      github_token_write: githubTokenWrite || undefined,
      enable_playwright: enablePlaywright || undefined,
      allowed_tools: allowedTools.length > 0 ? allowedTools : undefined,
      custom_env: Object.keys(customEnv).length > 0 ? customEnv : undefined,
      initial_prompt: initialPrompt || undefined,
      run_id: runId || undefined,
      dev_channels_server: devChannelsServer.trim() || undefined,
    }
  }

  const handleSpawnSession = async () => {
    if (!selectedConfig) {
      setError('Please select a snapshot or environment')
      return
    }
    setSpawning(true)
    try {
      const newSession = await spawnSession(buildSpawnConfig())
      await loadSessions()
      selectSession(newSession)
      setShowNewSession(false)
      // Reset form
      setSelectedRepoUrl('')
      setInitialPrompt('')
      setRunId('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to spawn session')
    } finally {
      setSpawning(false)
    }
  }

  const handleScheduleSession = async () => {
    if (!selectedConfig) {
      setError('Please select a snapshot or environment')
      return
    }
    if (!initialPrompt) {
      setError('Scheduled sessions require an initial prompt')
      return
    }
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
      setError(e instanceof Error ? e.message : 'Failed to schedule session')
    } finally {
      setScheduling(false)
    }
  }

  const handleNewSession = () => {
    selectSession(null)
    setAttachInfo(null)
    setShowNewSession(true)
  }

  // Render helpers
  const getStatusColor = (status: string | null) => {
    if (!status) return 'bg-slate-100 text-slate-600'
    return STATUS_COLORS[status.toLowerCase()] || 'bg-slate-100 text-slate-600'
  }

  return (
    <Page
      title={<>Claude Sessions{orchestratorAvailable === false && <Badge tone="danger">Orchestrator Offline</Badge>}</>}
      width="full"
      fill
      loading={loading}
      actions={<Button variant="primary" onClick={handleNewSession}>+ New Session</Button>}
    >
      <div className="h-full flex flex-col gap-4">
      {error && <Notice tone="error" onDismiss={() => setError(null)}>{error}</Notice>}
      <div className="flex-1 min-h-0 flex overflow-hidden bg-white rounded-xl border border-slate-200 shadow-sm">
        {/* Sidebar - Sessions list */}
        <aside className="w-72 shrink-0 border-r border-slate-200 flex flex-col">
          <div className="p-4 border-b border-slate-200">
            <h2 className="text-sm font-medium text-slate-600">Active Sessions</h2>
          </div>
          <div className="flex-1 overflow-y-auto">
            {sessions.length === 0 ? (
              <div className="p-4 text-sm text-slate-500 text-center">No active sessions</div>
            ) : (
              <ul className="divide-y divide-slate-100">
                {sessions.map((session) => (
                  <li
                    key={session.session_id}
                    className={`p-3 cursor-pointer hover:bg-slate-50 ${
                      selectedSession?.session_id === session.session_id ? 'bg-primary/5 border-l-2 border-primary' : ''
                    }`}
                    onClick={() => handleSelectSession(session)}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="font-mono text-sm text-slate-800 truncate">{session.session_id}</div>
                        <div className="flex items-center gap-2 mt-1">
                          <span className={`text-xs px-2 py-0.5 rounded ${getStatusColor(session.status)}`}>
                            {session.status || 'unknown'}
                          </span>
                        </div>
                      </div>
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          handleKillSession(session.session_id)
                        }}
                        disabled={killingId === session.session_id}
                        className="text-red-500 hover:text-red-700 p-1 rounded hover:bg-red-50 disabled:opacity-50"
                        title="Kill session"
                      >
                        {killingId === session.session_id ? (
                          <span className="text-xs">...</span>
                        ) : (
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              strokeWidth={2}
                              d="M6 18L18 6M6 6l12 12"
                            />
                          </svg>
                        )}
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>

        {/* Main panel */}
        <div className="flex-1 min-w-0 p-6 overflow-y-auto">
          {showNewSession ? (
            // New session form
            <div className="max-w-xl">
              <h2 className="text-xl font-semibold text-slate-800 mb-6">Start New Session</h2>

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
                <div className="space-y-6">
                  {/* Config source selection (grouped: environments + snapshots) */}
                  <div>
                    <Label className="mb-2">Configuration *</Label>
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
                    <p className="text-xs text-slate-500 mt-1">
                      {selectedConfig?.type === 'environment'
                        ? 'Environment: State persists across sessions'
                        : 'Snapshot: Fresh extraction each time'}
                    </p>
                  </div>

                  {/* Repo selection */}
                  <div>
                    <Label className="mb-2">Repository (optional)</Label>
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
                    <p className="text-xs text-slate-500 mt-1">The repo will be cloned into the workspace</p>
                  </div>

                  {/* Branch ID (only shown when repo selected) */}
                  {selectedRepoUrl && (
                    <div>
                      <Label className="mb-2">
                        Branch ID (optional)
                      </Label>
                      <Input
                        type="text"
                        value={runId}
                        onChange={(e) => setRunId(e.target.value)}
                        placeholder="my-feature"
                        pattern="[a-zA-Z0-9_-]+"
                        className="font-mono"
                      />
                      <p className="text-xs text-slate-500 mt-1">
                        Git branch will be <code className="bg-slate-100 px-1 rounded">claude/{runId || '<session-id>'}</code>. Leave blank for auto-generated ID.
                      </p>
                    </div>
                  )}

                  {/* GitHub Token */}
                  <div>
                    <Label className="mb-2">GitHub Token (optional)</Label>
                    <Input
                      type="password"
                      value={githubToken}
                      onChange={(e) => setGithubToken(e.target.value)}
                      placeholder="ghp_... or secret name"
                      className="font-mono"
                    />
                    <p className="text-xs text-slate-500 mt-1">
                      PAT for HTTPS clone, or a secret name. Cached in localStorage.
                    </p>
                  </div>

                  {/* GitHub Token Write */}
                  <div>
                    <Label className="mb-2">GitHub Write Token (optional)</Label>
                    <Input
                      type="password"
                      value={githubTokenWrite}
                      onChange={(e) => setGithubTokenWrite(e.target.value)}
                      placeholder="ghp_... or secret name"
                      className="font-mono"
                    />
                    <p className="text-xs text-slate-500 mt-1">
                      Write token for differ (push, PR creation). Cached in localStorage.
                    </p>
                  </div>

                  {/* Playwright toggle */}
                  <div className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      id="enable-playwright"
                      checked={enablePlaywright}
                      onChange={(e) => setEnablePlaywright(e.target.checked)}
                    />
                    <label htmlFor="enable-playwright" className="text-sm font-medium text-slate-700">
                      Enable Playwright
                    </label>
                    <span className="text-xs text-slate-500">(headless browser automation via MCP)</span>
                  </div>

                  {/* Dev channels server */}
                  <div>
                    <Label className="mb-2">Dev Channels Server (optional)</Label>
                    <Input
                      type="text"
                      value={devChannelsServer}
                      onChange={(e) => setDevChannelsServer(e.target.value)}
                      placeholder="http://channels.internal:3000"
                      className="font-mono"
                    />
                    <p className="text-xs text-slate-500 mt-1">
                      Launches Claude with --dangerously-load-development-channels. Leave blank to disable.
                    </p>
                  </div>

                  {/* Pre-approved Tools */}
                  <div>
                    <Label className="mb-2">
                      Pre-approved Tools
                    </Label>
                    <p className="text-xs text-slate-500 mb-3">
                      These tools won't require permission prompts. MCP tools always require permission.
                    </p>
                    <div className="grid grid-cols-3 gap-2 mb-3">
                      {COMMON_TOOLS.map(tool => (
                        <label key={tool} className="flex items-center gap-2 text-sm">
                          <input
                            type="checkbox"
                            checked={allowedTools.includes(tool)}
                            onChange={() => toggleTool(tool)}
                          />
                          {tool}
                        </label>
                      ))}
                    </div>
                  </div>

                  {/* Custom Environment Variables */}
                  <div>
                    <Label className="mb-2">
                      Custom Environment Variables
                    </Label>
                    <p className="text-xs text-slate-500 mb-2">
                      One per line: KEY=value. Lines starting with # are ignored.
                    </p>
                    <Textarea
                      value={customEnvText}
                      onChange={(e) => setCustomEnvText(e.target.value)}
                      placeholder="MY_VAR=some_value&#10;ANOTHER_VAR=123"
                      rows={3}
                      className="font-mono"
                    />
                  </div>

                  {/* Initial Prompt */}
                  <div>
                    <Label className="mb-2">
                      Initial Prompt (optional)
                    </Label>
                    <Textarea
                      value={initialPrompt}
                      onChange={(e) => setInitialPrompt(e.target.value)}
                      placeholder="Start Claude with this prompt..."
                      rows={3}
                    />
                    <p className="text-xs text-slate-500 mt-1">
                      If provided, Claude will start processing this prompt immediately.
                    </p>
                  </div>

                  {/* Cron Schedule */}
                  <div>
                    <Label className="mb-2">
                      Schedule (optional)
                    </Label>
                    <div className="flex gap-2 mb-2">
                      <Input
                        type="text"
                        value={cronExpression}
                        onChange={(e) => setCronExpression(e.target.value)}
                        placeholder="0 9 * * *"
                        className="flex-1 font-mono"
                      />
                    </div>
                    <p className="text-xs text-slate-500 mb-2">
                      Standard 5-field cron: minute hour day-of-month month day-of-week
                    </p>
                    <div className="flex gap-2 mb-2">
                      {[
                        { label: 'Daily 9am', value: '0 9 * * *' },
                        { label: 'Weekdays 9am', value: '0 9 * * 1-5' },
                        { label: 'Weekly Mon', value: '0 9 * * 1' },
                      ].map((preset) => (
                        <button
                          key={preset.label}
                          type="button"
                          onClick={() => setCronExpression(preset.value)}
                          className={`text-xs px-2 py-1 rounded border ${
                            cronExpression === preset.value
                              ? 'bg-primary/10 border-primary text-primary'
                              : 'border-slate-200 text-slate-600 hover:bg-slate-50'
                          }`}
                        >
                          {preset.label}
                        </button>
                      ))}
                      {cronExpression && (
                        <Button size="sm"
                          type="button"
                          onClick={() => setCronExpression('')}
                        >
                          Clear
                        </Button>
                      )}
                    </div>
                    {cronExpression && !initialPrompt && (
                      <p className="text-xs text-red-600 bg-red-50 px-2 py-1 rounded mb-1">
                        An initial prompt is required for scheduled sessions.
                      </p>
                    )}
                    {cronExpression && (
                      <p className="text-xs text-amber-600 bg-amber-50 px-2 py-1 rounded">
                        This will create a recurring schedule, not start a session now.
                      </p>
                    )}
                  </div>

                  {/* Schedule success message */}
                  {scheduleSuccess && (
                    <Notice tone="success">
                      <p className="font-medium">Schedule created</p>
                      <p className="text-sm mt-1">
                        Task ID: <code className="bg-green-100 px-1 rounded">{scheduleSuccess.task_id}</code>
                      </p>
                      <p className="text-sm mt-1">
                        Next run: {new Date(scheduleSuccess.next_scheduled_time).toLocaleString()}
                      </p>
                      <button
                        onClick={() => setScheduleSuccess(null)}
                        className="text-sm text-green-700 hover:underline mt-2"
                      >
                        Dismiss
                      </button>
                    </Notice>
                  )}

                  {/* Actions */}
                  <div className="flex gap-3">
                    <Button variant="primary"
                      onClick={cronExpression ? handleScheduleSession : handleSpawnSession}
                      disabled={spawning || scheduling || !selectedConfig}
                    >
                      {spawning
                        ? 'Starting...'
                        : scheduling
                          ? 'Scheduling...'
                          : cronExpression
                            ? 'Schedule Recurring Session'
                            : 'Start Session'}
                    </Button>
                    <Button
                      onClick={() => setShowNewSession(false)}
                    >
                      Cancel
                    </Button>
                  </div>
                </div>
              )}
            </div>
          ) : selectedSession ? (
            // Selected session details
            (() => {
              const display = sessionDisplay(selectedSession.session_id, environments, users)
              return (
            <div className="h-full flex flex-col">
              {/* Header */}
              <div className="flex items-center gap-3 mb-4">
                <button
                  onClick={() => selectSession(null)}
                  className="text-sm text-slate-600 hover:text-slate-800 underline-offset-2 hover:underline"
                  title="Back to fleet overview"
                >
                  ← Fleet
                </button>
                <div className="flex-1 min-w-0">
                  <h2 className="text-xl font-semibold text-slate-800 truncate">{display.title}</h2>
                  {display.subtitle && (
                    <div className="text-xs text-slate-500">{display.subtitle}</div>
                  )}
                </div>
                <span className={`text-sm px-3 py-1 rounded ${getStatusColor(selectedSession.status)}`}>
                  {selectedSession.status || 'unknown'}
                </span>
                <Button variant="danger"
                  onClick={() => handleKillSession(selectedSession.session_id)}
                  disabled={killingId === selectedSession.session_id}
                >
                  {killingId === selectedSession.session_id ? 'Killing...' : 'Kill Session'}
                </Button>
              </div>

              {/* Session info row */}
              <div className="flex gap-4 mb-4">
                <div className="bg-white rounded-lg border border-slate-200 px-4 py-3 flex-1">
                  <div className="text-xs text-slate-500 mb-1">Session ID</div>
                  <div className="font-mono text-sm text-slate-800">{selectedSession.session_id}</div>
                </div>
                <div className="bg-white rounded-lg border border-slate-200 px-4 py-3 flex-1">
                  <div className="text-xs text-slate-500 mb-1">Container</div>
                  <div className="font-mono text-sm text-slate-800">{selectedSession.container_name || 'N/A'}</div>
                </div>
              </div>

              {/* Resource stats — collapsed by default; the chart polls only
                  while expanded (mounted) so closed panels are zero cost. */}
              <details className="mb-4 bg-white rounded-lg border border-slate-200">
                <summary className="px-4 py-3 cursor-pointer text-sm font-medium text-slate-700 hover:bg-slate-50">
                  Resource Stats
                </summary>
                <div className="px-4 pb-4">
                  <SessionStatsPanel sessionId={selectedSession.session_id} />
                </div>
              </details>

              {/* Attach commands (collapsible) */}
              {attachInfo && (
                <details className="mb-4 bg-white rounded-lg border border-slate-200">
                  <summary className="px-4 py-3 cursor-pointer text-sm font-medium text-slate-700 hover:bg-slate-50">
                    Connect Commands
                  </summary>
                  <div className="px-4 pb-4 space-y-3">
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
                </details>
              )}

              {/* Terminal screen */}
              <div className="flex-1 flex flex-col min-h-0">
                <div className="flex items-center mb-2 gap-3">
                  <div className="text-sm font-medium text-slate-700 flex items-center gap-2">
                    <span>Terminal</span>
                    {wsConnected ? (
                      <span className="flex items-center gap-1 text-xs text-green-600">
                        <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                        Connected
                      </span>
                    ) : wsError ? (
                      <span className="text-xs px-2 py-0.5 bg-red-100 text-red-600 rounded">
                        {wsError}
                      </span>
                    ) : (
                      <span className="text-xs px-2 py-0.5 bg-yellow-100 text-yellow-600 rounded">
                        Connecting...
                      </span>
                    )}
                  </div>
                  {containerStats && <ContainerStatsBadge stats={containerStats} />}
                </div>
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
                <details className="mt-4 bg-white rounded-lg border border-slate-200">
                  <summary className="px-4 py-3 cursor-pointer text-sm font-medium text-slate-700 hover:bg-slate-50 flex items-center gap-2">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                    </svg>
                    Code Review (Differ)
                  </summary>
                  <div className="border-t border-slate-200">
                    <iframe
                      src={getDifferUrl(selectedSession.session_id)}
                      className="w-full border-0 rounded-b-lg"
                      style={{ height: '70vh', minHeight: '500px' }}
                      title="Differ Code Review"
                      sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
                    />
                  </div>
                </details>
              )}
            </div>
              )
            })()
          ) : (
            // Empty state — show fleet-wide resource overview while no session
            // is selected. Admins see global capacity + every container; non-
            // admins see only their own.
            <div className="max-w-4xl">
              <div className="flex items-baseline justify-between mb-4">
                <h2 className="text-xl font-semibold text-slate-800">Fleet Overview</h2>
                <p className="text-sm text-slate-500">
                  Select a session for details, or click a row below to see its history.
                </p>
              </div>
              <ClaudeFleetStats
                selectedSessionId={null}
                onSelectContainer={(id) => navigate(`/ui/claude/${id}`)}
                environments={environments}
                hasAdminScope={hasScope('*')}
                currentUserId={authUser?.id ?? null}
              />
            </div>
          )}
        </div>
      </div>
      </div>
    </Page>
  )
}

export default ClaudeSessions

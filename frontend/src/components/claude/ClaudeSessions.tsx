import { useState, useEffect, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import { useClaude, ClaudeSession, Snapshot, Environment, GithubRepoBasic, AttachInfo, getLogStreamUrl } from '../../hooks/useClaude'
import XtermTerminal from './XtermTerminal'

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
  type: 'screen' | 'error' | 'status'
  data: string
  timestamp: string
  cols?: number
  rows?: number
}

const STATUS_COLORS: Record<string, string> = {
  running: 'bg-green-100 text-green-700',
  created: 'bg-blue-100 text-blue-700',
  exited: 'bg-slate-100 text-slate-600',
  paused: 'bg-yellow-100 text-yellow-700',
}

const ClaudeSessions = () => {
  const {
    listSessions,
    killSession,
    spawnSession,
    getAttachInfo,
    getOrchestratorStatus,
    listSnapshots,
    listEnvironments,
    listUserRepos,
  } = useClaude()

  // State
  const [sessions, setSessions] = useState<ClaudeSession[]>([])
  const [selectedSession, setSelectedSession] = useState<ClaudeSession | null>(null)
  const [attachInfo, setAttachInfo] = useState<AttachInfo | null>(null)
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [environments, setEnvironments] = useState<Environment[]>([])
  const [repos, setRepos] = useState<GithubRepoBasic[]>([])
  const [orchestratorAvailable, setOrchestratorAvailable] = useState<boolean | null>(null)

  // Screen streaming state
  const [screenContent, setScreenContent] = useState<string>('')
  const [tmuxSize, setTmuxSize] = useState<{ cols: number; rows: number } | null>(null)
  const [wsConnected, setWsConnected] = useState(false)
  const [wsError, setWsError] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)

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
  const [useHappy, setUseHappy] = useState<boolean>(false)
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

  // Load data
  const loadSessions = useCallback(async () => {
    try {
      const data = await listSessions()
      setSessions(data)
      // If selected session no longer exists, clear it
      if (selectedSession && !data.find((s) => s.session_id === selectedSession.session_id)) {
        setSelectedSession(null)
        setAttachInfo(null)
      }
    } catch (e) {
      console.error('Failed to load sessions:', e)
    }
  }, [listSessions, selectedSession])

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
    setTmuxSize(null)
    setWsError(null)

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
          if (msg.cols && msg.rows) {
            setTmuxSize({ cols: msg.cols, rows: msg.rows })
          }
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
    setSelectedSession(session)
    setShowNewSession(false)
  }

  const handleKillSession = async (sessionId: string) => {
    if (!confirm('Kill this session? The container will be stopped and removed.')) return
    setKillingId(sessionId)
    try {
      await killSession(sessionId)
      if (selectedSession?.session_id === sessionId) {
        setSelectedSession(null)
        setAttachInfo(null)
      }
      await loadSessions()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to kill session')
    } finally {
      setKillingId(null)
    }
  }

  const handleSpawnSession = async () => {
    if (!selectedConfig) {
      setError('Please select a snapshot or environment')
      return
    }
    setSpawning(true)
    try {
      // Parse custom environment variables
      const customEnv = parseEnvText(customEnvText)

      const newSession = await spawnSession({
        snapshot_id: selectedConfig.type === 'snapshot' ? selectedConfig.id : undefined,
        environment_id: selectedConfig.type === 'environment' ? selectedConfig.id : undefined,
        repo_url: selectedRepoUrl || undefined,
        github_token: githubToken || undefined,
        github_token_write: githubTokenWrite || undefined,
        use_happy: useHappy || undefined,
        allowed_tools: allowedTools.length > 0 ? allowedTools : undefined,
        custom_env: Object.keys(customEnv).length > 0 ? customEnv : undefined,
        initial_prompt: initialPrompt || undefined,
      })
      await loadSessions()
      setSelectedSession(newSession)
      setShowNewSession(false)
      // Reset form
      setSelectedRepoUrl('')
      setUseHappy(false)
      setInitialPrompt('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to spawn session')
    } finally {
      setSpawning(false)
    }
  }

  const handleNewSession = () => {
    setSelectedSession(null)
    setAttachInfo(null)
    setShowNewSession(true)
  }

  // Render helpers
  const getStatusColor = (status: string | null) => {
    if (!status) return 'bg-slate-100 text-slate-600'
    return STATUS_COLORS[status.toLowerCase()] || 'bg-slate-100 text-slate-600'
  }

  // Check if Happy option should be shown
  // - Environments: always show (user is responsible for ensuring Happy works)
  // - Snapshots: only show if has_happy detected in snapshot summary
  const selectedConfigHasHappy = (): boolean => {
    if (!selectedConfig) return false
    if (selectedConfig.type === 'environment') return true
    const snapshot = snapshots.find((s) => s.id === selectedConfig.id)
    if (!snapshot?.summary) return false
    try {
      const summary = JSON.parse(snapshot.summary)
      return summary.has_happy === true
    } catch {
      return false
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-50 flex items-center justify-center">
        <div className="text-slate-500">Loading...</div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col">
      {/* Header */}
      <header className="bg-white border-b border-slate-200 px-6 py-4 flex items-center gap-4">
        <Link
          to="/ui/dashboard"
          className="bg-slate-50 text-slate-600 border border-slate-200 py-2 px-4 rounded-md text-sm hover:bg-slate-100"
        >
          Back
        </Link>
        <h1 className="text-2xl font-semibold text-slate-800 flex-1">Claude Sessions</h1>
        {orchestratorAvailable === false && (
          <span className="text-sm text-red-600 bg-red-50 px-3 py-1 rounded">Orchestrator Offline</span>
        )}
        <button
          onClick={handleNewSession}
          className="bg-primary text-white py-2 px-4 rounded-md text-sm hover:bg-primary/90"
        >
          + New Session
        </button>
      </header>

      {/* Error banner */}
      {error && (
        <div className="bg-red-50 border-b border-red-200 text-red-700 px-6 py-3 flex justify-between items-center">
          <p>{error}</p>
          <button onClick={() => setError(null)} className="text-red-700 hover:underline text-sm">
            Dismiss
          </button>
        </div>
      )}

      {/* Main content */}
      <div className="flex-1 flex">
        {/* Sidebar - Sessions list */}
        <aside className="w-72 bg-white border-r border-slate-200 flex flex-col">
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
        <main className="flex-1 p-6 overflow-y-auto">
          {showNewSession ? (
            // New session form
            <div className="max-w-xl">
              <h2 className="text-xl font-semibold text-slate-800 mb-6">Start New Session</h2>

              {snapshots.length === 0 && environments.length === 0 ? (
                <div className="bg-yellow-50 border border-yellow-200 text-yellow-800 p-4 rounded-lg">
                  <p className="font-medium">No snapshots or environments available</p>
                  <p className="text-sm mt-1">
                    Create a snapshot first using the CLI tool:
                    <code className="block bg-yellow-100 px-2 py-1 rounded mt-2 text-xs">
                      python tools/claude_snapshot.py --host $HOST --api-key $KEY
                    </code>
                  </p>
                </div>
              ) : (
                <div className="space-y-6">
                  {/* Config source selection (grouped: environments + snapshots) */}
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-2">Configuration *</label>
                    <select
                      value={selectedConfig ? `${selectedConfig.type}:${selectedConfig.id}` : ''}
                      onChange={(e) => {
                        const [type, id] = e.target.value.split(':')
                        if (type === 'environment' || type === 'snapshot') {
                          setSelectedConfig({ type, id: Number(id) })
                        }
                      }}
                      className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
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
                    </select>
                    <p className="text-xs text-slate-500 mt-1">
                      {selectedConfig?.type === 'environment'
                        ? 'Environment: State persists across sessions'
                        : 'Snapshot: Fresh extraction each time'}
                    </p>
                  </div>

                  {/* Repo selection */}
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-2">Repository (optional)</label>
                    <select
                      value={selectedRepoUrl}
                      onChange={(e) => setSelectedRepoUrl(e.target.value)}
                      className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                    >
                      <option value="">No repository</option>
                      {repos.map((r) => (
                        <option key={r.id} value={`git@github.com:${r.owner}/${r.name}.git`}>
                          {r.owner}/{r.name}
                        </option>
                      ))}
                    </select>
                    <p className="text-xs text-slate-500 mt-1">The repo will be cloned into the workspace</p>
                  </div>

                  {/* GitHub Token */}
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-2">GitHub Token (optional)</label>
                    <input
                      type="password"
                      value={githubToken}
                      onChange={(e) => setGithubToken(e.target.value)}
                      placeholder="ghp_... or secret name"
                      className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm font-mono focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                    />
                    <p className="text-xs text-slate-500 mt-1">
                      PAT for HTTPS clone, or a secret name. Cached in localStorage.
                    </p>
                  </div>

                  {/* GitHub Token Write */}
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-2">GitHub Write Token (optional)</label>
                    <input
                      type="password"
                      value={githubTokenWrite}
                      onChange={(e) => setGithubTokenWrite(e.target.value)}
                      placeholder="ghp_... or secret name"
                      className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm font-mono focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                    />
                    <p className="text-xs text-slate-500 mt-1">
                      Write token for differ (push, PR creation). Cached in localStorage.
                    </p>
                  </div>

                  {/* Happy toggle - only show if snapshot has Happy config */}
                  {selectedConfigHasHappy() && (
                    <div className="flex items-center gap-3">
                      <input
                        type="checkbox"
                        id="use-happy"
                        checked={useHappy}
                        onChange={(e) => setUseHappy(e.target.checked)}
                        className="w-4 h-4 text-primary border-slate-300 rounded focus:ring-primary"
                      />
                      <label htmlFor="use-happy" className="text-sm font-medium text-slate-700">
                        Run with Happy
                      </label>
                      <span className="text-xs text-slate-500">(mobile access via Happy app)</span>
                    </div>
                  )}

                  {/* Pre-approved Tools */}
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-2">
                      Pre-approved Tools
                    </label>
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
                            className="w-4 h-4 text-primary border-slate-300 rounded"
                          />
                          {tool}
                        </label>
                      ))}
                    </div>
                  </div>

                  {/* Custom Environment Variables */}
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-2">
                      Custom Environment Variables
                    </label>
                    <p className="text-xs text-slate-500 mb-2">
                      One per line: KEY=value. Lines starting with # are ignored.
                    </p>
                    <textarea
                      value={customEnvText}
                      onChange={(e) => setCustomEnvText(e.target.value)}
                      placeholder="MY_VAR=some_value&#10;ANOTHER_VAR=123"
                      rows={3}
                      className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm font-mono focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                    />
                  </div>

                  {/* Initial Prompt */}
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-2">
                      Initial Prompt (optional)
                    </label>
                    <textarea
                      value={initialPrompt}
                      onChange={(e) => setInitialPrompt(e.target.value)}
                      placeholder="Start Claude with this prompt..."
                      rows={3}
                      className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                    />
                    <p className="text-xs text-slate-500 mt-1">
                      If provided, Claude will start processing this prompt immediately.
                    </p>
                  </div>

                  {/* Actions */}
                  <div className="flex gap-3">
                    <button
                      onClick={handleSpawnSession}
                      disabled={spawning || !selectedConfig}
                      className="bg-primary text-white py-2 px-6 rounded-lg text-sm font-medium hover:bg-primary/90 disabled:bg-slate-300 disabled:cursor-not-allowed"
                    >
                      {spawning ? 'Starting...' : 'Start Session'}
                    </button>
                    <button
                      onClick={() => setShowNewSession(false)}
                      className="bg-slate-100 text-slate-600 py-2 px-6 rounded-lg text-sm font-medium hover:bg-slate-200"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          ) : selectedSession ? (
            // Selected session details
            <div className="h-full flex flex-col">
              {/* Header */}
              <div className="flex items-center gap-3 mb-4">
                <h2 className="text-xl font-semibold text-slate-800">Session Details</h2>
                <span className={`text-sm px-3 py-1 rounded ${getStatusColor(selectedSession.status)}`}>
                  {selectedSession.status || 'unknown'}
                </span>
                <div className="flex-1" />
                <button
                  onClick={() => handleKillSession(selectedSession.session_id)}
                  disabled={killingId === selectedSession.session_id}
                  className="bg-red-50 text-red-600 py-2 px-4 rounded-lg text-sm font-medium hover:bg-red-100 disabled:opacity-50"
                >
                  {killingId === selectedSession.session_id ? 'Killing...' : 'Kill Session'}
                </button>
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
                <div className="flex items-center mb-2">
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
                </div>
                <div className="flex-1 bg-slate-900 rounded-lg overflow-hidden min-h-[400px]">
                  <XtermTerminal
                    wsRef={wsRef}
                    screenContent={screenContent}
                    connected={wsConnected}
                    tmuxSize={tmuxSize}
                  />
                </div>
              </div>
            </div>
          ) : (
            // Empty state
            <div className="flex flex-col items-center justify-center h-full text-center">
              <div className="text-slate-400 mb-4">
                <svg className="w-16 h-16 mx-auto" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={1}
                    d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
                  />
                </svg>
              </div>
              <h3 className="text-lg font-medium text-slate-600 mb-2">No Session Selected</h3>
              <p className="text-sm text-slate-500 mb-4">Select a session from the sidebar or start a new one</p>
              <button
                onClick={handleNewSession}
                className="bg-primary text-white py-2 px-4 rounded-lg text-sm font-medium hover:bg-primary/90"
              >
                + New Session
              </button>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}

export default ClaudeSessions

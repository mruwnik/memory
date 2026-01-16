import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useClaude, ClaudeSession, Snapshot, GithubRepoBasic, AttachInfo } from '../../hooks/useClaude'

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
    listUserRepos,
  } = useClaude()

  // State
  const [sessions, setSessions] = useState<ClaudeSession[]>([])
  const [selectedSession, setSelectedSession] = useState<ClaudeSession | null>(null)
  const [attachInfo, setAttachInfo] = useState<AttachInfo | null>(null)
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [repos, setRepos] = useState<GithubRepoBasic[]>([])
  const [orchestratorAvailable, setOrchestratorAvailable] = useState<boolean | null>(null)

  // UI State
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showNewSession, setShowNewSession] = useState(false)
  const [killingId, setKillingId] = useState<string | null>(null)
  const [spawning, setSpawning] = useState(false)

  // New session form
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<number | null>(null)
  const [selectedRepoUrl, setSelectedRepoUrl] = useState<string>('')
  const [useHappy, setUseHappy] = useState<boolean>(false)

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
      const [sessionsData, snapshotsData, reposData, status] = await Promise.all([
        listSessions(),
        listSnapshots(),
        listUserRepos(),
        getOrchestratorStatus(),
      ])
      setSessions(sessionsData)
      setSnapshots(snapshotsData)
      setRepos(reposData)
      setOrchestratorAvailable(status.available)
      if (snapshotsData.length > 0) {
        setSelectedSnapshotId(snapshotsData[0].id)
      }
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [listSessions, listSnapshots, listUserRepos, getOrchestratorStatus])

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
    if (!selectedSnapshotId) {
      setError('Please select a snapshot')
      return
    }
    setSpawning(true)
    try {
      const newSession = await spawnSession({
        snapshot_id: selectedSnapshotId,
        repo_url: selectedRepoUrl || undefined,
        use_happy: useHappy || undefined,
      })
      await loadSessions()
      setSelectedSession(newSession)
      setShowNewSession(false)
      // Reset form
      setSelectedRepoUrl('')
      setUseHappy(false)
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

  // Check if selected snapshot has Happy config
  const selectedSnapshotHasHappy = (): boolean => {
    if (!selectedSnapshotId) return false
    const snapshot = snapshots.find((s) => s.id === selectedSnapshotId)
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

              {snapshots.length === 0 ? (
                <div className="bg-yellow-50 border border-yellow-200 text-yellow-800 p-4 rounded-lg">
                  <p className="font-medium">No snapshots available</p>
                  <p className="text-sm mt-1">
                    Create a snapshot first using the CLI tool:
                    <code className="block bg-yellow-100 px-2 py-1 rounded mt-2 text-xs">
                      python tools/claude_snapshot.py --host $HOST --api-key $KEY
                    </code>
                  </p>
                </div>
              ) : (
                <div className="space-y-6">
                  {/* Snapshot selection */}
                  <div>
                    <label className="block text-sm font-medium text-slate-700 mb-2">Config Snapshot *</label>
                    <select
                      value={selectedSnapshotId ?? ''}
                      onChange={(e) => setSelectedSnapshotId(Number(e.target.value))}
                      className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                    >
                      {snapshots.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                          {s.subscription_type && ` (${s.subscription_type})`}
                        </option>
                      ))}
                    </select>
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

                  {/* Happy toggle - only show if snapshot has Happy config */}
                  {selectedSnapshotHasHappy() && (
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

                  {/* Actions */}
                  <div className="flex gap-3">
                    <button
                      onClick={handleSpawnSession}
                      disabled={spawning || !selectedSnapshotId}
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
            <div className="max-w-2xl">
              <div className="flex items-center gap-3 mb-6">
                <h2 className="text-xl font-semibold text-slate-800">Session Details</h2>
                <span className={`text-sm px-3 py-1 rounded ${getStatusColor(selectedSession.status)}`}>
                  {selectedSession.status || 'unknown'}
                </span>
              </div>

              <div className="bg-white rounded-lg border border-slate-200 divide-y divide-slate-200">
                <div className="p-4">
                  <div className="text-sm text-slate-500 mb-1">Session ID</div>
                  <div className="font-mono text-slate-800">{selectedSession.session_id}</div>
                </div>
                <div className="p-4">
                  <div className="text-sm text-slate-500 mb-1">Container</div>
                  <div className="font-mono text-slate-800">{selectedSession.container_name || 'N/A'}</div>
                </div>
                {attachInfo && (
                  <>
                    <div className="p-4">
                      <div className="text-sm text-slate-500 mb-2">Connect to Session</div>
                      <p className="text-sm text-slate-600 mb-3">
                        SSH into the server and run one of these commands:
                      </p>
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
                      </div>
                      <p className="text-xs text-slate-500 mt-3">
                        Detach without killing: <kbd className="bg-slate-100 px-1 rounded">Ctrl+P</kbd> then{' '}
                        <kbd className="bg-slate-100 px-1 rounded">Ctrl+Q</kbd>
                      </p>
                    </div>
                  </>
                )}
              </div>

              <div className="mt-6">
                <button
                  onClick={() => handleKillSession(selectedSession.session_id)}
                  disabled={killingId === selectedSession.session_id}
                  className="bg-red-50 text-red-600 py-2 px-4 rounded-lg text-sm font-medium hover:bg-red-100 disabled:opacity-50"
                >
                  {killingId === selectedSession.session_id ? 'Killing...' : 'Kill Session'}
                </button>
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

import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useClaude, Snapshot, Environment, CreateEnvironmentRequest } from '../../hooks/useClaude'

interface SnapshotSummary {
  skills: string[]
  agents: string[]
  plugins: string[]
  hooks: string[]
  commands: string[]
  mcp_servers: string[]
  has_happy?: boolean
}

type Tab = 'environments' | 'snapshots'

const ConfigSources = () => {
  const {
    listSnapshots,
    listEnvironments,
    createEnvironment,
    deleteEnvironment,
    resetEnvironment,
  } = useClaude()

  const [activeTab, setActiveTab] = useState<Tab>('environments')
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [environments, setEnvironments] = useState<Environment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null) // "snap-{id}" or "env-{id}"

  // Create environment modal state
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [newEnvName, setNewEnvName] = useState('')
  const [newEnvDescription, setNewEnvDescription] = useState('')
  const [newEnvSourceType, setNewEnvSourceType] = useState<'empty' | 'snapshot' | 'environment'>('empty')
  const [newEnvSnapshotId, setNewEnvSnapshotId] = useState<number | null>(null)
  const [newEnvSourceEnvId, setNewEnvSourceEnvId] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)

  // Reset environment modal state
  const [resettingEnvId, setResettingEnvId] = useState<number | null>(null)
  const [resetSnapshotId, setResetSnapshotId] = useState<number | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [snapshotsData, environmentsData] = await Promise.all([
        listSnapshots(),
        listEnvironments(),
      ])
      setSnapshots(snapshotsData)
      setEnvironments(environmentsData)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [listSnapshots, listEnvironments])

  useEffect(() => {
    loadData()
  }, [loadData])

  const handleDeleteSnapshot = async (id: number) => {
    if (!confirm('Are you sure you want to delete this snapshot?')) return

    setDeletingId(`snap-${id}`)
    try {
      const response = await fetch(`/claude/snapshots/${id}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!response.ok) throw new Error('Failed to delete snapshot')
      loadData()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete snapshot')
    } finally {
      setDeletingId(null)
    }
  }

  const handleDeleteEnvironment = async (id: number) => {
    if (!confirm('Are you sure you want to delete this environment? All data in the volume will be lost.')) return

    setDeletingId(`env-${id}`)
    try {
      await deleteEnvironment(id)
      loadData()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete environment')
    } finally {
      setDeletingId(null)
    }
  }

  const handleCreateEnvironment = async () => {
    if (!newEnvName.trim()) {
      setError('Environment name is required')
      return
    }

    setCreating(true)
    try {
      const request: CreateEnvironmentRequest = {
        name: newEnvName.trim(),
        description: newEnvDescription.trim() || undefined,
        snapshot_id: newEnvSourceType === 'snapshot' ? (newEnvSnapshotId || undefined) : undefined,
        source_environment_id: newEnvSourceType === 'environment' ? (newEnvSourceEnvId || undefined) : undefined,
      }
      await createEnvironment(request)
      setShowCreateModal(false)
      setNewEnvName('')
      setNewEnvDescription('')
      setNewEnvSourceType('empty')
      setNewEnvSnapshotId(null)
      setNewEnvSourceEnvId(null)
      loadData()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to create environment')
    } finally {
      setCreating(false)
    }
  }

  const handleResetEnvironment = async () => {
    if (!resettingEnvId) return
    if (!confirm('Are you sure you want to reset this environment? All current data will be lost.')) return

    try {
      await resetEnvironment(resettingEnvId, { snapshot_id: resetSnapshotId || undefined })
      setResettingEnvId(null)
      setResetSnapshotId(null)
      loadData()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to reset environment')
    }
  }

  const formatSize = (bytes: number | null) => {
    if (bytes === null) return 'Unknown'
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return 'Never'
    const date = new Date(dateStr)
    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const parseSummary = (summaryStr: string | null): SnapshotSummary | null => {
    if (!summaryStr) return null
    try {
      return JSON.parse(summaryStr)
    } catch {
      return null
    }
  }

  const SUBSCRIPTION_COLORS: Record<string, string> = {
    max: 'bg-purple-100 text-purple-700',
    pro: 'bg-blue-100 text-blue-700',
    free: 'bg-slate-100 text-slate-700',
  }

  return (
    <div className="min-h-screen bg-slate-50 p-6">
      <header className="flex items-center gap-4 mb-6 pb-4 border-b border-slate-200">
        <Link
          to="/ui/dashboard"
          className="bg-slate-50 text-slate-600 border border-slate-200 py-2 px-4 rounded-md text-sm hover:bg-slate-100"
        >
          Back
        </Link>
        <h1 className="text-2xl font-semibold text-slate-800 flex-1">Claude Configurations</h1>
        <button
          onClick={() => loadData()}
          className="w-9 h-9 bg-white border border-slate-200 rounded-lg hover:bg-slate-50 text-lg"
          title="Refresh"
          aria-label="Refresh list"
        >
          &#8635;
        </button>
      </header>

      {/* Tabs */}
      <div className="flex gap-1 mb-6 bg-slate-200 p-1 rounded-lg w-fit">
        <button
          onClick={() => setActiveTab('environments')}
          className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
            activeTab === 'environments'
              ? 'bg-white text-slate-800 shadow-sm'
              : 'text-slate-600 hover:text-slate-800'
          }`}
        >
          Environments ({environments.length})
        </button>
        <button
          onClick={() => setActiveTab('snapshots')}
          className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
            activeTab === 'snapshots'
              ? 'bg-white text-slate-800 shadow-sm'
              : 'text-slate-600 hover:text-slate-800'
          }`}
        >
          Snapshots ({snapshots.length})
        </button>
      </div>

      <div className="space-y-4">
        {/* Error */}
        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-lg flex justify-between items-center">
            <p>{error}</p>
            <button onClick={() => setError(null)} className="text-red-700 hover:underline">
              Dismiss
            </button>
          </div>
        )}

        {/* Loading */}
        {loading && <div className="text-center py-8 text-slate-500">Loading...</div>}

        {/* Environments Tab */}
        {!loading && activeTab === 'environments' && (
          <>
            {/* Create Environment Button */}
            <div className="flex justify-end">
              <button
                onClick={() => setShowCreateModal(true)}
                className="bg-primary text-white py-2 px-4 rounded-md text-sm hover:bg-primary/90"
              >
                + New Environment
              </button>
            </div>

            {/* Empty State */}
            {environments.length === 0 && (
              <div className="text-center py-12 text-slate-500 bg-white rounded-xl">
                <p className="mb-4">No environments yet</p>
                <p className="text-sm">
                  Environments are persistent Docker volumes that retain state across sessions.
                  <br />
                  Create one to get started!
                </p>
              </div>
            )}

            {/* Environment List */}
            {environments.length > 0 && (
              <ul className="space-y-3">
                {environments.map((env) => (
                  <li key={env.id} className="bg-white p-4 rounded-lg shadow-sm border border-slate-200">
                    <div className="flex items-start gap-4">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="font-semibold text-slate-800">{env.name}</span>
                          <span className="px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-700">
                            Persistent
                          </span>
                        </div>

                        {env.description && (
                          <div className="text-sm text-slate-600">{env.description}</div>
                        )}

                        <div className="flex flex-wrap gap-3 mt-2 text-xs text-slate-500">
                          <span>Created: {formatDate(env.created_at)}</span>
                          <span>Last used: {formatDate(env.last_used_at)}</span>
                          <span>{env.session_count} session{env.session_count !== 1 ? 's' : ''}</span>
                          {env.size_bytes && <span>{formatSize(env.size_bytes)}</span>}
                        </div>

                        <div className="mt-2 text-xs text-slate-400 font-mono truncate">
                          Volume: {env.volume_name}
                        </div>
                      </div>

                      <div className="shrink-0 flex gap-2">
                        <button
                          onClick={() => {
                            setResettingEnvId(env.id)
                            setResetSnapshotId(env.initialized_from_snapshot_id)
                          }}
                          className="bg-yellow-50 text-yellow-600 py-1.5 px-3 rounded text-sm hover:bg-yellow-100"
                        >
                          Reset
                        </button>
                        <button
                          onClick={() => handleDeleteEnvironment(env.id)}
                          disabled={deletingId === `env-${env.id}`}
                          className="bg-red-50 text-red-600 py-1.5 px-3 rounded text-sm hover:bg-red-100 disabled:bg-slate-200 disabled:text-slate-400"
                        >
                          {deletingId === `env-${env.id}` ? 'Deleting...' : 'Delete'}
                        </button>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}

        {/* Snapshots Tab */}
        {!loading && activeTab === 'snapshots' && (
          <>
            {/* Empty State */}
            {snapshots.length === 0 && (
              <div className="text-center py-12 text-slate-500 bg-white rounded-xl">
                <p className="mb-4">No snapshots found</p>
                <p className="text-sm">
                  Use the CLI tool to create snapshots:
                  <br />
                  <code className="bg-slate-100 px-2 py-1 rounded mt-2 inline-block">
                    python tools/claude_snapshot.py --host $HOST --api-key $KEY
                  </code>
                </p>
              </div>
            )}

            {/* Snapshot List */}
            {snapshots.length > 0 && (
              <ul className="space-y-3">
                {snapshots.map((snapshot) => {
                  const summary = parseSummary(snapshot.summary)
                  const mcpCount = summary?.mcp_servers?.length ?? 0
                  const skillCount = summary?.skills?.length ?? 0
                  const agentCount = summary?.agents?.length ?? 0
                  const pluginCount = summary?.plugins?.length ?? 0

                  return (
                    <li key={snapshot.id} className="bg-white p-4 rounded-lg shadow-sm border border-slate-200">
                      <div className="flex items-start gap-4">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1">
                            <span className="font-semibold text-slate-800">{snapshot.name}</span>
                            {snapshot.subscription_type && (
                              <span
                                className={`px-2 py-0.5 rounded text-xs font-medium ${
                                  SUBSCRIPTION_COLORS[snapshot.subscription_type.toLowerCase()] ?? 'bg-slate-100 text-slate-600'
                                }`}
                              >
                                {snapshot.subscription_type}
                              </span>
                            )}
                            {summary?.has_happy && (
                              <span className="px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-700">
                                Happy
                              </span>
                            )}
                            <span className="px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-700">
                              Static
                            </span>
                          </div>

                          {snapshot.claude_account_email && (
                            <div className="text-sm text-slate-600">{snapshot.claude_account_email}</div>
                          )}

                          <div className="flex flex-wrap gap-3 mt-2 text-xs text-slate-500">
                            <span>{formatDate(snapshot.created_at)}</span>
                            <span>{formatSize(snapshot.size)}</span>
                            {mcpCount > 0 && (
                              <span className="text-primary">
                                {mcpCount} MCP server{mcpCount !== 1 ? 's' : ''}
                              </span>
                            )}
                            {skillCount > 0 && <span>{skillCount} skill{skillCount !== 1 ? 's' : ''}</span>}
                            {agentCount > 0 && <span>{agentCount} agent{agentCount !== 1 ? 's' : ''}</span>}
                            {pluginCount > 0 && <span>{pluginCount} plugin{pluginCount !== 1 ? 's' : ''}</span>}
                          </div>

                          <div className="mt-2 text-xs text-slate-400 font-mono truncate">
                            {snapshot.content_hash.slice(0, 16)}...
                          </div>
                        </div>

                        <div className="shrink-0">
                          <button
                            onClick={() => handleDeleteSnapshot(snapshot.id)}
                            disabled={deletingId === `snap-${snapshot.id}`}
                            className="bg-red-50 text-red-600 py-1.5 px-3 rounded text-sm hover:bg-red-100 disabled:bg-slate-200 disabled:text-slate-400"
                          >
                            {deletingId === `snap-${snapshot.id}` ? 'Deleting...' : 'Delete'}
                          </button>
                        </div>
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </>
        )}
      </div>

      {/* Create Environment Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-6">
            <h2 className="text-xl font-semibold text-slate-800 mb-4">Create Environment</h2>

            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Name *</label>
                <input
                  type="text"
                  value={newEnvName}
                  onChange={(e) => setNewEnvName(e.target.value)}
                  placeholder="My Development Environment"
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Description</label>
                <textarea
                  value={newEnvDescription}
                  onChange={(e) => setNewEnvDescription(e.target.value)}
                  placeholder="Optional description..."
                  rows={2}
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Initialize from</label>
                <div className="flex gap-2 mb-2">
                  <button
                    type="button"
                    onClick={() => setNewEnvSourceType('empty')}
                    className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                      newEnvSourceType === 'empty'
                        ? 'bg-primary text-white'
                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                    }`}
                  >
                    Empty
                  </button>
                  <button
                    type="button"
                    onClick={() => setNewEnvSourceType('snapshot')}
                    className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                      newEnvSourceType === 'snapshot'
                        ? 'bg-primary text-white'
                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                    }`}
                  >
                    Snapshot
                  </button>
                  <button
                    type="button"
                    onClick={() => setNewEnvSourceType('environment')}
                    className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                      newEnvSourceType === 'environment'
                        ? 'bg-primary text-white'
                        : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                    }`}
                  >
                    Environment
                  </button>
                </div>

                {newEnvSourceType === 'snapshot' && (
                  <select
                    value={newEnvSnapshotId ?? ''}
                    onChange={(e) => setNewEnvSnapshotId(e.target.value ? Number(e.target.value) : null)}
                    className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                  >
                    <option value="">Select a snapshot...</option>
                    {snapshots.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                )}

                {newEnvSourceType === 'environment' && (
                  <select
                    value={newEnvSourceEnvId ?? ''}
                    onChange={(e) => setNewEnvSourceEnvId(e.target.value ? Number(e.target.value) : null)}
                    className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                  >
                    <option value="">Select an environment...</option>
                    {environments.map((env) => (
                      <option key={env.id} value={env.id}>
                        {env.name}
                      </option>
                    ))}
                  </select>
                )}

                <p className="text-xs text-slate-500 mt-1">
                  {newEnvSourceType === 'empty' && 'Create a blank environment'}
                  {newEnvSourceType === 'snapshot' && 'Initialize with config from a snapshot'}
                  {newEnvSourceType === 'environment' && 'Clone all data from an existing environment'}
                </p>
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => {
                  setShowCreateModal(false)
                  setNewEnvName('')
                  setNewEnvDescription('')
                  setNewEnvSourceType('empty')
                  setNewEnvSnapshotId(null)
                  setNewEnvSourceEnvId(null)
                }}
                className="bg-slate-100 text-slate-600 py-2 px-4 rounded-lg text-sm font-medium hover:bg-slate-200"
              >
                Cancel
              </button>
              <button
                onClick={handleCreateEnvironment}
                disabled={creating || !newEnvName.trim()}
                className="bg-primary text-white py-2 px-4 rounded-lg text-sm font-medium hover:bg-primary/90 disabled:bg-slate-300 disabled:cursor-not-allowed"
              >
                {creating ? 'Creating...' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reset Environment Modal */}
      {resettingEnvId !== null && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-6">
            <h2 className="text-xl font-semibold text-slate-800 mb-4">Reset Environment</h2>
            <p className="text-sm text-slate-600 mb-4">
              This will delete all current data in the environment and optionally reinitialize from a snapshot.
            </p>

            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Reinitialize from Snapshot</label>
              <select
                value={resetSnapshotId ?? ''}
                onChange={(e) => setResetSnapshotId(e.target.value ? Number(e.target.value) : null)}
                className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
              >
                <option value="">Empty (no snapshot)</option>
                {snapshots.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <button
                onClick={() => {
                  setResettingEnvId(null)
                  setResetSnapshotId(null)
                }}
                className="bg-slate-100 text-slate-600 py-2 px-4 rounded-lg text-sm font-medium hover:bg-slate-200"
              >
                Cancel
              </button>
              <button
                onClick={handleResetEnvironment}
                className="bg-red-500 text-white py-2 px-4 rounded-lg text-sm font-medium hover:bg-red-600"
              >
                Reset Environment
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default ConfigSources

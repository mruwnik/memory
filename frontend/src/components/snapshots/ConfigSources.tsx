import { useState, useEffect, useCallback } from 'react'
import { useClaude, Snapshot, Environment, CreateEnvironmentRequest } from '../../hooks/useClaude'
import { Button, EmptyState, Input, Label, LoadingState, Modal, Notice, Page, Select, Tabs, Textarea, ToggleGroup } from '@/components/shared/ui'

interface SnapshotSummary {
  skills: string[]
  agents: string[]
  plugins: string[]
  hooks: string[]
  commands: string[]
  mcp_servers: string[]
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
    <Page
      title="Claude Configurations"
      actions={<>
        <Button variant="primary" onClick={() => setShowCreateModal(true)}>New environment</Button>
        <Button size="icon" onClick={() => loadData()} title="Refresh" aria-label="Refresh list">&#8635;</Button>
      </>}
      width="wide"
    >
      {/* Tabs */}
      <Tabs label="Sections" value={activeTab} onChange={setActiveTab} options={[{ value: 'environments', label: <>Environments ({environments.length})</> }, { value: 'snapshots', label: <>Snapshots ({snapshots.length})</> }]} />

      <div className="space-y-4">
        {/* Error */}
        {error && (
          <Notice tone="error" onDismiss={() => setError(null)}>{error}</Notice>
        )}

        {/* Loading */}
        {loading && <LoadingState />}

        {/* Environments Tab */}
        {!loading && activeTab === 'environments' && (
          <>
            {/* Empty State */}
            {environments.length === 0 && (
              <EmptyState message={<>
                <p className="mb-4">No environments yet</p>
                <p className="text-sm">
                  Environments are persistent Docker volumes that retain state across sessions.
                  <br />
                  Create one to get started!
                </p>
              </>} />
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
                        <Button variant="danger" size="sm"
                          onClick={() => handleDeleteEnvironment(env.id)}
                          disabled={deletingId === `env-${env.id}`}
                        >
                          {deletingId === `env-${env.id}` ? 'Deleting...' : 'Delete'}
                        </Button>
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
              <EmptyState message={<>
                <p className="mb-4">No snapshots found</p>
                <p className="text-sm">
                  Use the CLI tool to create snapshots:
                  <br />
                  <code className="bg-slate-100 px-2 py-1 rounded mt-2 inline-block">
                    python tools/claude_snapshot.py --host $HOST --api-key $KEY
                  </code>
                </p>
              </>} />
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
                          <Button variant="danger" size="sm"
                            onClick={() => handleDeleteSnapshot(snapshot.id)}
                            disabled={deletingId === `snap-${snapshot.id}`}
                          >
                            {deletingId === `snap-${snapshot.id}` ? 'Deleting...' : 'Delete'}
                          </Button>
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
        <Modal title="Create Environment" onClose={() => {
                  setShowCreateModal(false)
                  setNewEnvName('')
                  setNewEnvDescription('')
                  setNewEnvSourceType('empty')
                  setNewEnvSnapshotId(null)
                  setNewEnvSourceEnvId(null)
                }} size="md">
          <div className="space-y-4">
              <div>
                <Label className="mb-1">Name *</Label>
                <Input
                  type="text"
                  value={newEnvName}
                  onChange={(e) => setNewEnvName(e.target.value)}
                  placeholder="My Development Environment"
                />
              </div>

              <div>
                <Label className="mb-1">Description</Label>
                <Textarea
                  value={newEnvDescription}
                  onChange={(e) => setNewEnvDescription(e.target.value)}
                  placeholder="Optional description..."
                  rows={2}
                />
              </div>

              <div>
                <Label className="mb-1">Initialize from</Label>
                <ToggleGroup label="Initialize from" value={newEnvSourceType} onChange={setNewEnvSourceType} options={[{ value: 'empty', label: 'Empty' }, { value: 'snapshot', label: 'Snapshot' }, { value: 'environment', label: 'Environment' }]} />

                {newEnvSourceType === 'snapshot' && (
                  <Select
                    value={newEnvSnapshotId ?? ''}
                    onChange={(e) => setNewEnvSnapshotId(e.target.value ? Number(e.target.value) : null)}
                  >
                    <option value="">Select a snapshot...</option>
                    {snapshots.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </Select>
                )}

                {newEnvSourceType === 'environment' && (
                  <Select
                    value={newEnvSourceEnvId ?? ''}
                    onChange={(e) => setNewEnvSourceEnvId(e.target.value ? Number(e.target.value) : null)}
                  >
                    <option value="">Select an environment...</option>
                    {environments.map((env) => (
                      <option key={env.id} value={env.id}>
                        {env.name}
                      </option>
                    ))}
                  </Select>
                )}

                <p className="text-xs text-slate-500 mt-1">
                  {newEnvSourceType === 'empty' && 'Create a blank environment'}
                  {newEnvSourceType === 'snapshot' && 'Initialize with config from a snapshot'}
                  {newEnvSourceType === 'environment' && 'Clone all data from an existing environment'}
                </p>
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <Button
                onClick={() => {
                  setShowCreateModal(false)
                  setNewEnvName('')
                  setNewEnvDescription('')
                  setNewEnvSourceType('empty')
                  setNewEnvSnapshotId(null)
                  setNewEnvSourceEnvId(null)
                }}
              >
                Cancel
              </Button>
              <Button variant="primary"
                onClick={handleCreateEnvironment}
                disabled={creating || !newEnvName.trim()}
              >
                {creating ? 'Creating...' : 'Create'}
              </Button>
            </div>
        </Modal>
      )}

      {/* Reset Environment Modal */}
      {resettingEnvId !== null && (
        <Modal title="Reset Environment" onClose={() => {
                  setResettingEnvId(null)
                  setResetSnapshotId(null)
                }} size="md">
          <p className="text-sm text-slate-600 mb-4">
              This will delete all current data in the environment and optionally reinitialize from a snapshot.
            </p>

            <div>
              <Label className="mb-1">Reinitialize from Snapshot</Label>
              <Select
                value={resetSnapshotId ?? ''}
                onChange={(e) => setResetSnapshotId(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">Empty (no snapshot)</option>
                {snapshots.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <Button
                onClick={() => {
                  setResettingEnvId(null)
                  setResetSnapshotId(null)
                }}
              >
                Cancel
              </Button>
              <Button variant="dangerSolid"
                onClick={handleResetEnvironment}
              >
                Reset Environment
              </Button>
            </div>
        </Modal>
      )}
    </Page>
  )
}

export default ConfigSources

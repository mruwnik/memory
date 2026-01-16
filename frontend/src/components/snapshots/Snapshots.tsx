import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'

interface Snapshot {
  id: number
  name: string
  content_hash: string
  claude_account_email: string | null
  subscription_type: string | null
  summary: string | null
  filename: string
  size: number
  created_at: string | null
}

interface SnapshotSummary {
  skills: string[]
  agents: string[]
  plugins: string[]
  hooks: string[]
  commands: string[]
  mcp_servers: string[]
  has_happy?: boolean
}

const Snapshots = () => {
  const [snapshots, setSnapshots] = useState<Snapshot[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<number | null>(null)

  const loadSnapshots = useCallback(async () => {
    setLoading(true)
    try {
      const response = await fetch('/claude/snapshots/list', {
        credentials: 'include',
      })
      if (!response.ok) throw new Error('Failed to load snapshots')
      const data = await response.json()
      setSnapshots(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load snapshots')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadSnapshots()
  }, [loadSnapshots])

  const handleDelete = async (id: number) => {
    if (!confirm('Are you sure you want to delete this snapshot?')) return

    setDeletingId(id)
    try {
      const response = await fetch(`/claude/snapshots/${id}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!response.ok) throw new Error('Failed to delete snapshot')
      loadSnapshots()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete snapshot')
    } finally {
      setDeletingId(null)
    }
  }

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return 'Unknown'
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
        <h1 className="text-2xl font-semibold text-slate-800 flex-1">Claude Config Snapshots</h1>
        <button
          onClick={() => loadSnapshots()}
          className="w-9 h-9 bg-white border border-slate-200 rounded-lg hover:bg-slate-50 text-lg"
          title="Refresh"
          aria-label="Refresh snapshots list"
        >
          &#8635;
        </button>
      </header>

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
        {loading && <div className="text-center py-8 text-slate-500">Loading snapshots...</div>}

        {/* Empty State */}
        {!loading && snapshots.length === 0 && (
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
        {!loading && snapshots.length > 0 && (
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
                        onClick={() => handleDelete(snapshot.id)}
                        disabled={deletingId === snapshot.id}
                        className="bg-red-50 text-red-600 py-1.5 px-3 rounded text-sm hover:bg-red-100 disabled:bg-slate-200 disabled:text-slate-400"
                      >
                        {deletingId === snapshot.id ? 'Deleting...' : 'Delete'}
                      </button>
                    </div>
                  </div>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}

export default Snapshots

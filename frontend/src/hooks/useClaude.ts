import { useCallback } from 'react'
import { useAuth } from './useAuth'

export interface ClaudeSession {
  session_id: string
  container_id: string | null
  container_name: string | null
  status: string | null
}

export interface Snapshot {
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

export interface AttachInfo {
  session_id: string
  container_name: string
  attach_cmd: string
  exec_cmd: string
}

export interface OrchestratorStatus {
  available: boolean
  socket_path: string | null
}

export interface SpawnRequest {
  snapshot_id: number
  repo_url?: string
  use_happy?: boolean
}

export interface GithubRepoBasic {
  id: number
  owner: string
  name: string
  repo_path: string
}

export const useClaude = () => {
  const { apiCall } = useAuth()

  // Session management
  const listSessions = useCallback(async (): Promise<ClaudeSession[]> => {
    const response = await apiCall('/claude/list')
    if (!response.ok) throw new Error('Failed to list sessions')
    return response.json()
  }, [apiCall])

  const getSession = useCallback(async (sessionId: string): Promise<ClaudeSession> => {
    const response = await apiCall(`/claude/${sessionId}`)
    if (!response.ok) throw new Error('Failed to get session')
    return response.json()
  }, [apiCall])

  const spawnSession = useCallback(async (request: SpawnRequest): Promise<ClaudeSession> => {
    const response = await apiCall('/claude/spawn', {
      method: 'POST',
      body: JSON.stringify(request),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to spawn session')
    }
    return response.json()
  }, [apiCall])

  const killSession = useCallback(async (sessionId: string): Promise<void> => {
    const response = await apiCall(`/claude/${sessionId}`, { method: 'DELETE' })
    if (!response.ok) throw new Error('Failed to kill session')
  }, [apiCall])

  const getAttachInfo = useCallback(async (sessionId: string): Promise<AttachInfo> => {
    const response = await apiCall(`/claude/${sessionId}/attach`)
    if (!response.ok) throw new Error('Failed to get attach info')
    return response.json()
  }, [apiCall])

  const getOrchestratorStatus = useCallback(async (): Promise<OrchestratorStatus> => {
    const response = await apiCall('/claude/status')
    if (!response.ok) throw new Error('Failed to get orchestrator status')
    return response.json()
  }, [apiCall])

  // Snapshots (for selecting config when spawning)
  const listSnapshots = useCallback(async (): Promise<Snapshot[]> => {
    const response = await apiCall('/claude/snapshots/list')
    if (!response.ok) throw new Error('Failed to list snapshots')
    return response.json()
  }, [apiCall])

  // GitHub repos (for selecting repo when spawning)
  const listUserRepos = useCallback(async (): Promise<GithubRepoBasic[]> => {
    const response = await apiCall('/github/accounts')
    if (!response.ok) throw new Error('Failed to list GitHub accounts')
    const accounts = await response.json()
    // Flatten repos from all accounts
    const repos: GithubRepoBasic[] = []
    for (const account of accounts) {
      for (const repo of account.repos || []) {
        repos.push({
          id: repo.id,
          owner: repo.owner,
          name: repo.name,
          repo_path: repo.repo_path,
        })
      }
    }
    return repos
  }, [apiCall])

  return {
    listSessions,
    getSession,
    spawnSession,
    killSession,
    getAttachInfo,
    getOrchestratorStatus,
    listSnapshots,
    listUserRepos,
  }
}

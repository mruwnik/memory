import { useCallback } from 'react'
import { useAuth } from './useAuth'
import { apiError } from './apiError'

export interface NotesSyncStatus {
  ok: boolean
  failed_step: string | null
  hint: string | null
  at: string
}

export interface SSHProbe {
  remote_url: string
  host: string
  port: number
  keys: { public_key: string; fingerprint: string; algorithm: string }[]
}

export interface NotesGitSetup {
  has_repo: boolean
  branch: string | null
  remote_url: string | null
  /** The vault already has a remote; changing it requires confirmation. */
  remote_locked: boolean
  author_name: string | null
  author_email: string | null
  /** Write-only: only the last four characters ever come back. */
  token: { set: boolean; hint: string | null; locked: boolean }
  ssh_public_key: string | null
  ssh?: { key_id: number; public_key: string; remote_url: string; host_keys: string } | null
}

export interface NotesSource {
  count: number
  last_sync: NotesSyncStatus | null
  /** Admins only. */
  git: NotesGitSetup | null
}

export interface NotesGitSetupInput {
  branch: string
  confirm_destination_change: boolean
  remote_url: string | null
  author_name: string
  author_email: string
  token: string | null
}

export const useNotesSource = () => {
  const { apiCall } = useAuth()

  const getNotesSource = useCallback(async (): Promise<NotesSource> => {
    const response = await apiCall('/notes-source')
    if (!response.ok) throw await apiError(response, 'Failed to load notes')
    return response.json()
  }, [apiCall])

  const setUpNotesGit = useCallback(async (input: NotesGitSetupInput): Promise<NotesSource> => {
    const response = await apiCall('/notes-source/git', { method: 'PUT', body: JSON.stringify(input) })
    if (!response.ok) throw await apiError(response, 'Failed to set up the git sync')
    return response.json()
  }, [apiCall])

  const clearNotesGitToken = useCallback(async (): Promise<NotesSource> => {
    const response = await apiCall('/notes-source/git/token', { method: 'DELETE' })
    if (!response.ok) throw await apiError(response, 'Failed to clear the token')
    return response.json()
  }, [apiCall])

  const setUpNotesSSH = useCallback(async (remote_url: string, host_keys: string, key_id: number | null): Promise<NotesSource> => {
    const response = await apiCall('/notes-source/git/ssh', { method: 'PUT', body: JSON.stringify({ remote_url, host_keys, key_id }) })
    if (!response.ok) throw await apiError(response, 'Failed to configure SSH')
    return response.json()
  }, [apiCall])

  const syncNotes = useCallback(async (): Promise<void> => {
    const response = await apiCall('/notes-source/sync', { method: 'POST' })
    if (!response.ok) throw await apiError(response, 'Failed to start a sync')
  }, [apiCall])

  const probeNotesSSH = useCallback(async (remote_url: string): Promise<SSHProbe> => {
    const response = await apiCall('/notes-source/git/ssh/probe', { method: 'POST', body: JSON.stringify({ remote_url }) })
    if (!response.ok) throw await apiError(response, 'Failed to check SSH server')
    return response.json()
  }, [apiCall])

  return { getNotesSource, setUpNotesGit, clearNotesGitToken, syncNotes, setUpNotesSSH, probeNotesSSH }
}

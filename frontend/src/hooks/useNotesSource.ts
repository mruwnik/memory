import { useCallback } from 'react'
import { useAuth } from './useAuth'
import { apiError } from './apiError'

export interface NotesSyncStatus {
  ok: boolean
  failed_step: string | null
  hint: string | null
  at: string
}

export interface NotesGitSetup {
  has_repo: boolean
  branch: string | null
  remote_url: string | null
  /** The vault already has a remote, which Memory won't repoint. */
  remote_locked: boolean
  author_name: string | null
  author_email: string | null
  /** Write-only: only the last four characters ever come back. */
  token: { set: boolean; hint: string | null; locked: boolean }
  ssh_public_key: string | null
}

export interface NotesSource {
  count: number
  last_sync: NotesSyncStatus | null
  /** Admins only. */
  git: NotesGitSetup | null
}

export interface NotesGitSetupInput {
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

  const syncNotes = useCallback(async (): Promise<void> => {
    const response = await apiCall('/notes-source/sync', { method: 'POST' })
    if (!response.ok) throw await apiError(response, 'Failed to start a sync')
  }, [apiCall])

  return { getNotesSource, setUpNotesGit, clearNotesGitToken, syncNotes }
}

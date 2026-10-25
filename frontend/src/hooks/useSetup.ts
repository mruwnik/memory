import { useCallback } from 'react'
import { SERVER_URL, useAuth } from './useAuth'
import { apiError } from './apiError'

export type SourceKind = 'email' | 'google' | 'github' | 'slack' | 'discord' | 'feeds' | 'notes' | 'transcripts'

export interface SetupStatus {
  needs_account: boolean
  sources: Record<SourceKind, number>
  mcp_clients: string[]
  mcp_url: string
}

export interface AccountCreate {
  name: string
  email: string
  password: string
  token: string
}

/** Whether this instance still has no account; asked before anyone signs in. */
export const getNeedsAccount = async (): Promise<boolean> => {
  const response = await fetch(`${SERVER_URL}/setup/status`)
  if (!response.ok) throw await apiError(response, 'Could not check whether this server is set up')
  return (await response.json()).needs_account
}

/** Creates the first account and returns a session token signed in as it. */
export const createAccount = async (body: AccountCreate): Promise<string> => {
  const response = await fetch(`${SERVER_URL}/setup/account`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) throw await apiError(response, 'Could not create the account')
  return (await response.json()).session_id
}

export const useSetup = () => {
  const { apiCall } = useAuth()

  const getStatus = useCallback(async (): Promise<SetupStatus> => {
    const response = await apiCall('/setup/status')
    if (!response.ok) throw await apiError(response, 'Could not load setup progress')
    return response.json()
  }, [apiCall])

  return { getStatus }
}

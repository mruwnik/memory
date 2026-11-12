import { useCallback } from 'react'
import { useAuth } from './useAuth'
import { apiError } from './apiError'

export type AccountKind = 'discord' | 'slack' | 'github' | 'email'
/** A Discord link code can be phished, so it never makes a record a suggestion. */
export type ReasonKind = Exclude<AccountKind, 'discord'>
export type NotifyChannel = 'discord' | 'slack' | 'email'

export interface IdentityPerson {
  id: number
  identifier: string
  display_name: string
}

export interface LinkedAccount {
  kind: AccountKind
  id: string
  label: string
  /** Proven by the user's own credential (Slack OAuth, GitHub login, email account, Discord link code). */
  verified: boolean
  /** The user can unlink it here: the Person lists it, or it is a Discord account they verified. */
  removable: boolean
  /** The user's Person has it; a verified Discord account can sit on another Person. */
  on_person: boolean
}

export interface IdentitySuggestion {
  person: IdentityPerson
  /** A GitHub reason's value is the numeric GitHub user id. */
  reasons: { kind: ReasonKind; value: string }[]
}

export interface Identity {
  person: IdentityPerson | null
  accounts: LinkedAccount[]
  suggestions: IdentitySuggestion[]
  notify: {
    order: NotifyChannel[]
    chosen: NotifyChannel | null
    problems: Partial<Record<NotifyChannel, string>>
  }
}

export interface DiscordLinkCode {
  code: string
  expires_at: string
  /** Bots the user may DM the code to. */
  bots: string[]
}

export const useIdentity = () => {
  const { apiCall } = useAuth()

  const request = useCallback(async (path: string, method = 'GET', body?: unknown) => {
    const response = await apiCall(`/auth/me/identity${path}`, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
    if (!response.ok) throw await apiError(response, 'Identity request failed')
    return response.json()
  }, [apiCall])

  const getIdentity = useCallback((): Promise<Identity> => request(''), [request])
  const claimPerson = useCallback((personId: number): Promise<Identity> => request('/claim', 'POST', { person_id: personId }), [request])
  const unlinkAccount = useCallback((kind: AccountKind, id: string): Promise<Identity> =>
    request(`/accounts/${kind}/${encodeURIComponent(id)}`, 'DELETE'), [request])
  const discordLinkCode = useCallback((): Promise<DiscordLinkCode> => request('/discord-code', 'POST'), [request])

  return { getIdentity, claimPerson, unlinkAccount, discordLinkCode }
}

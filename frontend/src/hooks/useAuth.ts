import { useState, useEffect, useCallback } from 'react'

export const SERVER_URL = import.meta.env.VITE_SERVER_URL || ''
export const SESSION_COOKIE_NAME = import.meta.env.VITE_SESSION_COOKIE_NAME || 'session_id'

const AUTH_LOST_EVENT = 'memory:auth-lost'

// Cookie utilities
const getCookie = (name: string) => {
  const value = `; ${document.cookie}`
  const parts = value.split(`; ${name}=`)
  if (parts.length === 2) return parts.pop().split(';').shift()
  return null
}

const setCookie = (name: string, value: string, days = 30) => {
  const expires = new Date()
  expires.setTime(expires.getTime() + days * 24 * 60 * 60 * 1000)
  document.cookie = `${name}=${value};expires=${expires.toUTCString()};path=/;SameSite=Lax`
}

const deleteCookie = (name: string) => {
  document.cookie = `${name}=;expires=Thu, 01 Jan 1970 00:00:01 GMT;path=/`
}

const getClientId = () => localStorage.getItem('oauth_client_id')

// Every useAuth instance listens for this, so whichever call notices first
// signs the whole page out once.
const dropAuth = () => {
  deleteCookie('access_token')
  deleteCookie('refresh_token')
  deleteCookie(SESSION_COOKIE_NAME)
  localStorage.removeItem('oauth_client_id')
  window.dispatchEvent(new Event(AUTH_LOST_EVENT))
}

const authedFetch = (endpoint: string, options: RequestInit, accessToken: string) => {
  // Don't set Content-Type for FormData - browser sets it with boundary
  const headers: Record<string, string> = { Authorization: `Bearer ${accessToken}` }
  if (!(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }
  return fetch(`${SERVER_URL}${endpoint}`, {
    ...options,
    headers: { ...headers, ...(options.headers as Record<string, string>) },
  })
}

const requestRefresh = async (): Promise<string | null> => {
  const refreshToken = getCookie('refresh_token')
  const clientId = getClientId()
  if (!refreshToken || !clientId) return null

  const response = await fetch(`${SERVER_URL}/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'refresh_token',
      refresh_token: refreshToken,
      client_id: clientId,
    }),
  })
  if (!response.ok) return null

  const tokens = await response.json()
  setCookie('access_token', tokens.access_token, 30)
  setCookie(SESSION_COOKIE_NAME, tokens.access_token, 30)
  if (tokens.refresh_token) {
    setCookie('refresh_token', tokens.refresh_token, 30)
  }
  return tokens.access_token as string
}

// Refresh tokens are single-use and the server revokes the whole family when
// one is replayed, so concurrent 401s (and tabs, which share the cookies)
// must not each spend the same refresh token.
let refreshInFlight: Promise<string | null> | null = null

const refreshOnce = (staleAccessToken: string) => {
  if (refreshInFlight) return refreshInFlight

  const refresh = async () => {
    const current = getCookie('access_token')
    if (current && current !== staleAccessToken) return current
    try {
      return await requestRefresh()
    } catch (error) {
      console.error('Token refresh failed:', error)
      return null
    }
  }
  const locked = navigator.locks
    ? navigator.locks.request('memory-token-refresh', refresh)
    : refresh()

  refreshInFlight = locked.then((accessToken) => {
    refreshInFlight = null
    if (!accessToken) dropAuth()
    return accessToken
  })
  return refreshInFlight
}

export interface AuthUser {
  id: number
  name: string
  email: string
  user_type: 'human' | 'bot'
  scopes: string[]
  timezone: string
}

export const useAuth = () => {
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [user, setUser] = useState<AuthUser | null>(null)

  useEffect(() => {
    const onAuthLost = () => {
      setIsAuthenticated(false)
      setUser(null)
      setIsLoading(false)
    }
    window.addEventListener(AUTH_LOST_EVENT, onAuthLost)
    return () => window.removeEventListener(AUTH_LOST_EVENT, onAuthLost)
  }, [])

  // Make authenticated API calls, refreshing the token at most once on 401
  const apiCall = useCallback(async (endpoint: string, options: RequestInit = {}) => {
    const accessToken = getCookie('access_token')
    if (!accessToken) {
      throw new Error('No access token available')
    }

    const response = await authedFetch(endpoint, options, accessToken)
    if (response.status !== 401) return response
    const freshToken = await refreshOnce(accessToken)
    return freshToken ? authedFetch(endpoint, options, freshToken) : response
  }, [])

  // Check if user has valid authentication
  const checkAuth = useCallback(async () => {
    const accessToken = getCookie('access_token')
    const sessionId = getCookie(SESSION_COOKIE_NAME)
    if (!accessToken && !sessionId) {
      setIsAuthenticated(false)
      setIsLoading(false)
      return false
    }

    let response: Response | null = null
    let userData = null
    try {
      response = await apiCall('/auth/me')
      if (response.ok) userData = await response.json()
    } catch (error) {
      console.error('Auth check failed:', error)
    }

    // Only a 401 is a verdict on the session; dropping the tokens every tab
    // shares over a network blip or a 5xx would sign them all out.
    if (response?.status === 401) dropAuth()
    if (!userData) {
      setIsAuthenticated(false)
      setIsLoading(false)
      return false
    }

    setUser({
      id: userData.user_id,
      name: userData.name,
      email: userData.email,
      user_type: userData.user_type,
      scopes: userData.scopes || [],
      timezone: userData.timezone,
    })
    setIsAuthenticated(true)
    setIsLoading(false)
    return true
  }, [apiCall])

  // Logout goes straight to the server: refreshing a session only to delete it
  // would just spend the refresh token.
  const logout = useCallback(async () => {
    const accessToken = getCookie('access_token')
    if (accessToken) {
      try {
        await authedFetch('/auth/logout', {}, accessToken)
      } catch (error) {
        console.error('Logout failed:', error)
      }
    }
    dropAuth()
  }, [])

  // Check if user has a specific scope
  const hasScope = useCallback((scope: string): boolean => {
    if (!user?.scopes) return false
    return user.scopes.includes('*') || user.scopes.includes(scope)
  }, [user])

  useEffect(() => {
    checkAuth()
  }, [checkAuth])

  return {
    isAuthenticated,
    isLoading,
    user,
    logout,
    checkAuth,
    apiCall,
    hasScope,
  }
}

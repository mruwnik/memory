import { useState, useEffect, useCallback } from 'react'

const SERVER_URL = import.meta.env.VITE_SERVER_URL || 'http://localhost:8000'
const SESSION_COOKIE_NAME = import.meta.env.VITE_SESSION_COOKIE_NAME || 'session_id'

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

export const useAuth = () => {
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [isLoading, setIsLoading] = useState(true)

  // Check if user has valid authentication
  const checkAuth = useCallback(async () => {
    const accessToken = getCookie('access_token')
    const sessionId = getCookie(SESSION_COOKIE_NAME)
    if (!accessToken && !sessionId) {
      setIsAuthenticated(false)
      setIsLoading(false)
      return false
    }

    try {
      // Validate token by making a test request
      const response = await apiCall('/auth/me')

      if (response.ok) {
        setIsAuthenticated(true)
        setIsLoading(false)
        return true
      } else {
        // Token is invalid, clear it
        logout()
        return false
      }
    } catch (error) {
      console.error('Auth check failed:', error)
      logout()
      return false
    }
  }, [])

  // Logout function
  const logout = useCallback(async () => {
    try {
      await apiCall('/auth/logout')
    } catch (error) {
      console.error('Logout failed:', error)
    }

    deleteCookie('access_token')
    deleteCookie('refresh_token')
    deleteCookie(SESSION_COOKIE_NAME)
    localStorage.removeItem('oauth_client_id')
    setIsAuthenticated(false)
  }, [])

  // Refresh access token using refresh token
  const refreshToken = useCallback(async () => {
    const refreshToken = getCookie('refresh_token')
    const clientId = getClientId()

    if (!refreshToken || !clientId) {
      logout()
      return false
    }

    try {
      const response = await apiCall('/token', {
        method: 'POST',
        body: {
          grant_type: 'refresh_token',
          refresh_token: refreshToken,
          client_id: clientId,
        },
      })

      if (response.ok) {
        const tokens = await response.json()
        setCookie('access_token', tokens.access_token, 30)
        if (tokens.refresh_token) {
          setCookie('refresh_token', tokens.refresh_token, 30)
        }
        return true
      } else {
        logout()
        return false
      }
    } catch (error) {
      console.error('Token refresh failed:', error)
      logout()
      return false
    }
  }, [logout])

  // Make authenticated API calls with automatic token refresh
  const apiCall = useCallback(async (endpoint: string, options: RequestInit = {}) => {
    let accessToken = getCookie('access_token')

    if (!accessToken) {
      throw new Error('No access token available')
    }

    const defaultHeaders = {
      'Authorization': `Bearer ${accessToken}`,
      'Content-Type': 'application/json',
    }

    const requestOptions: RequestInit & { headers: Record<string, string> } = {
      ...options,
      headers: { ...defaultHeaders, ...options.headers },
    }

    try {
      let response = await fetch(`${SERVER_URL}${endpoint}`, requestOptions)

      // If unauthorized, try refreshing token once
      if (response.status === 401) {
        const refreshed = await refreshToken()
        if (refreshed) {
          accessToken = getCookie('access_token')
          requestOptions.headers['Authorization'] = `Bearer ${accessToken}`
          response = await fetch(`${SERVER_URL}${endpoint}`, requestOptions)
        }
      }

      return response
    } catch (error) {
      console.error('API call failed:', error)
      throw error
    }
  }, [refreshToken])

  useEffect(() => {
    checkAuth()
  }, [checkAuth])

  return {
    isAuthenticated,
    isLoading,
    logout,
    checkAuth,
    apiCall,
    refreshToken,
  }
} 
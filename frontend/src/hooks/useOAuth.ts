import { useState, useCallback } from 'react'

const SERVER_URL = import.meta.env.VITE_SERVER_URL || 'http://localhost:8000'
const SESSION_COOKIE_NAME = import.meta.env.VITE_SESSION_COOKIE_NAME || 'session_id'
const REDIRECT_URI = `${window.location.origin}/ui`

// OAuth utilities
const generateCodeVerifier = () => {
  const array = new Uint8Array(32)
  crypto.getRandomValues(array)
  return btoa(String.fromCharCode(...array))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '')
}

const generateCodeChallenge = async (verifier: string) => {
  const data = new TextEncoder().encode(verifier)
  const digest = await crypto.subtle.digest('SHA-256', data)
  return btoa(String.fromCharCode(...new Uint8Array(digest)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '')
}

const generateState = () => {
  const array = new Uint8Array(16)
  crypto.getRandomValues(array)
  return btoa(String.fromCharCode(...array))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=/g, '')
}

// Storage utilities
const setCookie = (name: string, value: string, days = 30) => {
  const expires = new Date()
  expires.setTime(expires.getTime() + days * 24 * 60 * 60 * 1000)
  document.cookie = `${name}=${value};expires=${expires.toUTCString()};path=/;SameSite=Lax`
}

const getClientId = () => localStorage.getItem('oauth_client_id')
const setClientId = (clientId) => localStorage.setItem('oauth_client_id', clientId)

export const useOAuth = () => {
  const [error, setError] = useState<string | null>(null)

  // Register OAuth client with the server
  const registerClient = useCallback(async () => {
    try {
      const response = await fetch(`${SERVER_URL}/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          client_name: 'React Memory App',
          redirect_uris: [REDIRECT_URI],
          grant_types: ['authorization_code', 'refresh_token'],
          response_types: ['code'],
          scope: 'read write',
          token_endpoint_auth_method: 'none'
        })
      })

      if (response.ok) {
        const clientInfo = await response.json()
        setClientId(clientInfo.client_id)
        return clientInfo.client_id
      }
      return null
    } catch (error) {
      console.error('Error registering client:', error)
      return null
    }
  }, [])

  // Start OAuth authorization flow
  const startOAuth = useCallback(async () => {
    setError(null)

    let clientId = getClientId()
    if (!clientId) {
      clientId = await registerClient()
      if (!clientId) {
        setError('Failed to register OAuth client')
        return
      }
    }

    const state = generateState()
    const codeVerifier = generateCodeVerifier()
    const codeChallenge = await generateCodeChallenge(codeVerifier)

    // Store for callback verification
    localStorage.setItem('oauth_state', state)
    localStorage.setItem('code_verifier', codeVerifier)

    // Build authorization URL
    const authUrl = new URL(`${SERVER_URL}/authorize`)
    authUrl.searchParams.set('response_type', 'code')
    authUrl.searchParams.set('client_id', clientId)
    authUrl.searchParams.set('redirect_uri', REDIRECT_URI)
    authUrl.searchParams.set('scope', 'read write')
    authUrl.searchParams.set('state', state)
    authUrl.searchParams.set('code_challenge', codeChallenge)
    authUrl.searchParams.set('code_challenge_method', 'S256')

    window.location.href = authUrl.toString()
  }, [registerClient])

  // Handle OAuth callback
  const handleCallback = useCallback(async () => {
    const urlParams = new URLSearchParams(window.location.search)
    const code = urlParams.get('code')
    const state = urlParams.get('state')
    const error = urlParams.get('error')

    if (error) {
      setError(`OAuth error: ${error}`)
      return false
    }

    if (!code || !state) return false

    // Verify state
    const storedState = localStorage.getItem('oauth_state')
    const storedCodeVerifier = localStorage.getItem('code_verifier')
    const clientId = getClientId()

    if (state !== storedState) {
      setError('Invalid state parameter')
      return false
    }

    if (!clientId) {
      setError('Client ID not found')
      return false
    }

    try {
      // Exchange code for tokens
      const response = await fetch(`${SERVER_URL}/token`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          grant_type: 'authorization_code',
          client_id: clientId,
          code,
          redirect_uri: REDIRECT_URI,
          code_verifier: storedCodeVerifier || undefined
        })
      })

      if (response.ok) {
        const tokens = await response.json()

        // Store tokens
        setCookie('access_token', tokens.access_token, 30)
        setCookie(SESSION_COOKIE_NAME, tokens.access_token, 30)
        if (tokens.refresh_token) {
          setCookie('refresh_token', tokens.refresh_token, 30)
        }

        // Cleanup
        localStorage.removeItem('oauth_state')
        localStorage.removeItem('code_verifier')
        window.history.replaceState({}, document.title, window.location.pathname)

        return true
      } else {
        const errorData = await response.json()
        setError(`Token exchange failed: ${errorData.error || 'Unknown error'}`)
        return false
      }
    } catch (err) {
      setError(`Network error: ${err.message}`)
      return false
    }
  }, [])

  const clearError = useCallback(() => {
    setError(null)
    localStorage.removeItem('oauth_client_id') // Force re-registration on retry
  }, [])

  return {
    error,
    startOAuth,
    handleCallback,
    clearError
  }
} 
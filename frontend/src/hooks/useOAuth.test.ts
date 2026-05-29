import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useOAuth } from './useOAuth'
import { mockFetch, mockResponse, clearCookies } from '@/test/utils'

const origLocation = window.location

beforeEach(() => {
  clearCookies()
  localStorage.clear()
  vi.spyOn(console, 'error').mockImplementation(() => {})
  vi.spyOn(console, 'warn').mockImplementation(() => {})
  // Replaceable location so startOAuth can assign href without navigating.
  Object.defineProperty(window, 'location', {
    configurable: true,
    writable: true,
    value: {
      ...origLocation,
      origin: 'https://app.test',
      pathname: '/ui',
      search: '',
      href: 'https://app.test/ui',
      replaceState: vi.fn(),
    },
  })
})

afterEach(() => {
  Object.defineProperty(window, 'location', {
    configurable: true,
    writable: true,
    value: origLocation,
  })
})

describe('startOAuth', () => {
  it('registers a client when none is stored, then redirects to /authorize', async () => {
    const fetchMock = mockFetch(async (input) => {
      if (input.toString().includes('/register')) {
        return mockResponse({ json: { client_id: 'client-xyz' } })
      }
      return mockResponse({ json: {} })
    })
    const { result } = renderHook(() => useOAuth())
    await act(async () => {
      await result.current.startOAuth()
    })
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/register'),
      expect.objectContaining({ method: 'POST' }),
    )
    expect(localStorage.getItem('oauth_client_id')).toBe('client-xyz')
    expect(localStorage.getItem('oauth_state')).toBeTruthy()
    expect(localStorage.getItem('code_verifier')).toBeTruthy()
    expect(window.location.href).toContain('/authorize')
    expect(window.location.href).toContain('client_id=client-xyz')
    expect(window.location.href).toContain('code_challenge_method=S256')
  })

  it('reuses an existing client id without re-registering', async () => {
    localStorage.setItem('oauth_client_id', 'existing')
    const fetchMock = mockFetch(async () => mockResponse({ json: {} }))
    const { result } = renderHook(() => useOAuth())
    await act(async () => {
      await result.current.startOAuth()
    })
    expect(fetchMock).not.toHaveBeenCalled()
    expect(window.location.href).toContain('client_id=existing')
  })

  it('sets an error and does not redirect when registration fails', async () => {
    mockFetch(async (input) => {
      if (input.toString().includes('/register')) {
        return mockResponse({ status: 500 })
      }
      return mockResponse({ json: {} })
    })
    const { result } = renderHook(() => useOAuth())
    await act(async () => {
      await result.current.startOAuth()
    })
    expect(result.current.error).toBe('Failed to register OAuth client')
    expect(window.location.href).toBe('https://app.test/ui')
  })

  it('sets an error when registration throws', async () => {
    mockFetch(async () => {
      throw new Error('boom')
    })
    const { result } = renderHook(() => useOAuth())
    await act(async () => {
      await result.current.startOAuth()
    })
    expect(result.current.error).toBe('Failed to register OAuth client')
  })
})

describe('handleCallback', () => {
  it('returns false when there is no code/state and no error', async () => {
    window.location.search = ''
    mockFetch()
    const { result } = renderHook(() => useOAuth())
    let outcome!: boolean
    await act(async () => {
      outcome = await result.current.handleCallback()
    })
    expect(outcome).toBe(false)
  })

  it('sets an error and returns false when the URL has an OAuth error', async () => {
    window.location.search = '?error=access_denied'
    mockFetch()
    const { result } = renderHook(() => useOAuth())
    let outcome!: boolean
    await act(async () => {
      outcome = await result.current.handleCallback()
    })
    expect(outcome).toBe(false)
    expect(result.current.error).toBe('OAuth error: access_denied')
  })

  it('rejects a mismatched state parameter', async () => {
    window.location.search = '?code=abc&state=evil'
    localStorage.setItem('oauth_state', 'good')
    localStorage.setItem('oauth_client_id', 'client-1')
    mockFetch()
    const { result } = renderHook(() => useOAuth())
    let outcome!: boolean
    await act(async () => {
      outcome = await result.current.handleCallback()
    })
    expect(outcome).toBe(false)
    expect(result.current.error).toBe('Invalid state parameter')
  })

  it('errors when client id is missing', async () => {
    window.location.search = '?code=abc&state=good'
    localStorage.setItem('oauth_state', 'good')
    mockFetch()
    const { result } = renderHook(() => useOAuth())
    let outcome!: boolean
    await act(async () => {
      outcome = await result.current.handleCallback()
    })
    expect(outcome).toBe(false)
    expect(result.current.error).toBe('Client ID not found')
  })

  it('exchanges the code, stores tokens, and cleans up on success', async () => {
    window.location.search = '?code=abc&state=good'
    localStorage.setItem('oauth_state', 'good')
    localStorage.setItem('code_verifier', 'verifier')
    localStorage.setItem('oauth_client_id', 'client-1')
    const fetchMock = mockFetch(async (input) => {
      if (input.toString().includes('/token')) {
        return mockResponse({
          json: { access_token: 'at', refresh_token: 'rt' },
        })
      }
      return mockResponse({ json: {} })
    })
    const replaceSpy = vi.spyOn(window.history, 'replaceState')
    const { result } = renderHook(() => useOAuth())
    let outcome!: boolean
    await act(async () => {
      outcome = await result.current.handleCallback()
    })
    expect(outcome).toBe(true)
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/token'),
      expect.objectContaining({ method: 'POST' }),
    )
    expect(document.cookie).toContain('access_token=at')
    expect(document.cookie).toContain('session_id=at')
    expect(document.cookie).toContain('refresh_token=rt')
    expect(localStorage.getItem('oauth_state')).toBeNull()
    expect(localStorage.getItem('code_verifier')).toBeNull()
    expect(replaceSpy).toHaveBeenCalled()
    replaceSpy.mockRestore()
  })

  it('sets an error from the response body when token exchange fails', async () => {
    window.location.search = '?code=abc&state=good'
    localStorage.setItem('oauth_state', 'good')
    localStorage.setItem('oauth_client_id', 'client-1')
    mockFetch(async () =>
      mockResponse({ status: 400, json: { error: 'invalid_grant' } }),
    )
    const { result } = renderHook(() => useOAuth())
    let outcome!: boolean
    await act(async () => {
      outcome = await result.current.handleCallback()
    })
    expect(outcome).toBe(false)
    expect(result.current.error).toBe('Token exchange failed: invalid_grant')
  })

  it('sets a network error when the exchange throws', async () => {
    window.location.search = '?code=abc&state=good'
    localStorage.setItem('oauth_state', 'good')
    localStorage.setItem('oauth_client_id', 'client-1')
    mockFetch(async () => {
      throw new Error('offline')
    })
    const { result } = renderHook(() => useOAuth())
    let outcome!: boolean
    await act(async () => {
      outcome = await result.current.handleCallback()
    })
    expect(outcome).toBe(false)
    expect(result.current.error).toBe('Network error: offline')
  })
})

describe('clearError', () => {
  it('clears the error and removes the stored client id', async () => {
    localStorage.setItem('oauth_client_id', 'client-1')
    window.location.search = '?error=denied'
    mockFetch()
    const { result } = renderHook(() => useOAuth())
    await act(async () => {
      await result.current.handleCallback()
    })
    expect(result.current.error).toBeTruthy()
    act(() => {
      result.current.clearError()
    })
    expect(result.current.error).toBeNull()
    expect(localStorage.getItem('oauth_client_id')).toBeNull()
  })
})

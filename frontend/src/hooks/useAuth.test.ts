import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useAuth } from './useAuth'
import { mockFetch, mockResponse, clearCookies } from '@/test/utils'

const setCookie = (name: string, value: string) => {
  document.cookie = `${name}=${value};path=/`
}

beforeEach(() => {
  clearCookies()
  localStorage.clear()
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

describe('hasScope', () => {
  it('returns false when there is no user', () => {
    mockFetch()
    const { result } = renderHook(() => useAuth())
    expect(result.current.hasScope('teams')).toBe(false)
  })

  it('returns true for an exact scope match and a wildcard, false otherwise', async () => {
    setCookie('access_token', 'tok')
    mockFetch(async () =>
      mockResponse({
        json: {
          user_id: 1,
          name: 'A',
          email: 'a@x.com',
          user_type: 'human',
          scopes: ['teams'],
        },
      }),
    )
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true))
    expect(result.current.hasScope('teams')).toBe(true)
    expect(result.current.hasScope('admin')).toBe(false)
  })

  it('wildcard scope grants any scope', async () => {
    setCookie('access_token', 'tok')
    mockFetch(async () =>
      mockResponse({
        json: {
          user_id: 1,
          name: 'A',
          email: 'a@x.com',
          user_type: 'human',
          scopes: ['*'],
        },
      }),
    )
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true))
    expect(result.current.hasScope('anything')).toBe(true)
  })
})

describe('checkAuth', () => {
  it('sets unauthenticated and stops loading when no tokens exist', async () => {
    const fetchMock = mockFetch()
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.isAuthenticated).toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('authenticates and populates user on a successful /auth/me', async () => {
    setCookie('access_token', 'tok')
    mockFetch(async () =>
      mockResponse({
        json: {
          user_id: 7,
          name: 'Grace',
          email: 'grace@x.com',
          user_type: 'human',
          scopes: ['teams'],
        },
      }),
    )
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true))
    expect(result.current.user).toEqual({
      id: 7,
      name: 'Grace',
      email: 'grace@x.com',
      user_type: 'human',
      scopes: ['teams'],
    })
    expect(result.current.isLoading).toBe(false)
  })

  it('defaults scopes to [] when /auth/me omits them', async () => {
    setCookie('access_token', 'tok')
    mockFetch(async () =>
      mockResponse({
        json: {
          user_id: 7,
          name: 'Grace',
          email: 'grace@x.com',
          user_type: 'human',
        },
      }),
    )
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true))
    expect(result.current.user?.scopes).toEqual([])
  })

  it('logs out when /auth/me returns non-ok and no refresh token is present', async () => {
    setCookie('access_token', 'tok')
    // /auth/me 401 triggers refreshToken (no refresh cookie -> logout); the
    // subsequent /auth/logout is also routed below.
    const fetchMock = mockFetch(async (input) => {
      const url = input.toString()
      if (url.includes('/auth/logout')) return mockResponse({ json: {} })
      return mockResponse({ status: 401 })
    })
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.isAuthenticated).toBe(false))
    expect(fetchMock).toHaveBeenCalled()
  })

  it('stays unauthenticated and stops loading when apiCall throws (session cookie but no access token)', async () => {
    // session cookie present but no access_token -> apiCall throws -> catch -> logout.
    // The catch branch must clear isLoading so the app doesn't hang on a loading
    // spinner forever after an auth failure.
    setCookie('session_id', 'sess')
    const fetchMock = mockFetch()
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.isAuthenticated).toBe(false))
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    // /auth/me was never reached (apiCall threw before fetch); only logout fires.
    const meCalls = fetchMock.mock.calls.filter(([u]) =>
      u.toString().includes('/auth/me'),
    )
    expect(meCalls).toHaveLength(0)
  })

  it('stops loading when /auth/me returns non-ok', async () => {
    // The non-ok else branch must also clear isLoading (not just authentication).
    setCookie('access_token', 'tok')
    mockFetch(async (input) => {
      const url = input.toString()
      if (url.includes('/auth/logout')) return mockResponse({ json: {} })
      return mockResponse({ status: 401 })
    })
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.isAuthenticated).toBe(false)
  })
})

describe('apiCall', () => {
  it('throws when no access token is available', async () => {
    mockFetch()
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    await expect(result.current.apiCall('/x')).rejects.toThrow(
      'No access token available',
    )
  })

  it('attaches a Bearer Authorization and JSON content-type by default', async () => {
    setCookie('access_token', 'mytoken')
    const fetchMock = mockFetch(async () => mockResponse({ json: {} }))
    const { result } = renderHook(() => useAuth())
    await act(async () => {
      await result.current.apiCall('/foo')
    })
    const call = fetchMock.mock.calls.find(([u]) =>
      u.toString().includes('/foo'),
    )!
    const headers = (call[1] as RequestInit).headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer mytoken')
    expect(headers['Content-Type']).toBe('application/json')
  })

  it('omits Content-Type for FormData bodies', async () => {
    setCookie('access_token', 'mytoken')
    const fetchMock = mockFetch(async () => mockResponse({ json: {} }))
    const { result } = renderHook(() => useAuth())
    const fd = new FormData()
    fd.append('a', 'b')
    await act(async () => {
      await result.current.apiCall('/upload', { method: 'POST', body: fd })
    })
    const call = fetchMock.mock.calls.find(([u]) =>
      u.toString().includes('/upload'),
    )!
    const headers = (call[1] as RequestInit).headers as Record<string, string>
    expect(headers['Content-Type']).toBeUndefined()
    expect(headers.Authorization).toBe('Bearer mytoken')
  })

  it('refreshes the token on 401 then retries with the new token', async () => {
    setCookie('access_token', 'old')
    setCookie('refresh_token', 'refresh')
    localStorage.setItem('oauth_client_id', 'client-1')

    let fooCalls = 0
    const fetchMock = mockFetch(async (input) => {
      const url = input.toString()
      if (url.includes('/token')) {
        // Refresh succeeds and rotates the access token.
        document.cookie = 'access_token=new;path=/'
        return mockResponse({ json: { access_token: 'new' } })
      }
      if (url.includes('/foo')) {
        fooCalls += 1
        // First attempt unauthorized, second succeeds.
        return mockResponse({ status: fooCalls === 1 ? 401 : 200, json: {} })
      }
      return mockResponse({ json: {} })
    })

    const { result } = renderHook(() => useAuth())
    let response!: Response
    await act(async () => {
      response = await result.current.apiCall('/foo')
    })
    expect(response.status).toBe(200)
    expect(fooCalls).toBe(2)
    // The retry used the refreshed token.
    const retryCall = fetchMock.mock.calls
      .filter(([u]) => u.toString().includes('/foo'))
      .at(-1)!
    const headers = (retryCall[1] as RequestInit).headers as Record<
      string,
      string
    >
    expect(headers.Authorization).toBe('Bearer new')
  })

  it('returns the 401 response unchanged when refresh fails', async () => {
    setCookie('access_token', 'old')
    // No refresh token / client id -> refreshToken returns false (and logs out).
    const fetchMock = mockFetch(async (input) => {
      const url = input.toString()
      if (url.includes('/auth/logout')) return mockResponse({ json: {} })
      return mockResponse({ status: 401 })
    })
    const { result } = renderHook(() => useAuth())
    let response!: Response
    await act(async () => {
      response = await result.current.apiCall('/foo')
    })
    expect(response.status).toBe(401)
    // No retry: /foo fetched only once.
    const fooCalls = fetchMock.mock.calls.filter(([u]) =>
      u.toString().includes('/foo'),
    )
    expect(fooCalls).toHaveLength(1)
  })

  it('rethrows network errors', async () => {
    setCookie('access_token', 'tok')
    mockFetch(async () => {
      throw new Error('network down')
    })
    const { result } = renderHook(() => useAuth())
    await expect(result.current.apiCall('/foo')).rejects.toThrow('network down')
  })
})

describe('refreshToken', () => {
  it('logs out and returns false when refresh token or client id is missing', async () => {
    setCookie('access_token', 'tok')
    const fetchMock = mockFetch(async () => mockResponse({ json: {} }))
    const { result } = renderHook(() => useAuth())
    let outcome!: boolean
    await act(async () => {
      outcome = await result.current.refreshToken()
    })
    expect(outcome).toBe(false)
  })

  it('stores new tokens and returns true on success', async () => {
    setCookie('refresh_token', 'r1')
    localStorage.setItem('oauth_client_id', 'client-1')
    mockFetch(async (input) => {
      if (input.toString().includes('/token')) {
        document.cookie = 'access_token=at;path=/'
        return mockResponse({
          json: { access_token: 'at', refresh_token: 'r2' },
        })
      }
      return mockResponse({ json: {} })
    })
    // refreshToken's POST to /token goes through apiCall which needs an
    // access_token; seed one so apiCall does not throw.
    document.cookie = 'access_token=seed;path=/'
    const { result } = renderHook(() => useAuth())
    let outcome!: boolean
    await act(async () => {
      outcome = await result.current.refreshToken()
    })
    expect(outcome).toBe(true)
    expect(document.cookie).toContain('access_token=at')
    expect(document.cookie).toContain('refresh_token=r2')
  })

  it('logs out and returns false when the token endpoint returns non-ok', async () => {
    document.cookie = 'access_token=seed;path=/'
    setCookie('refresh_token', 'r1')
    localStorage.setItem('oauth_client_id', 'client-1')
    mockFetch(async (input) => {
      if (input.toString().includes('/token')) {
        return mockResponse({ status: 400 })
      }
      return mockResponse({ json: {} })
    })
    const { result } = renderHook(() => useAuth())
    let outcome!: boolean
    await act(async () => {
      outcome = await result.current.refreshToken()
    })
    expect(outcome).toBe(false)
  })
})

describe('logout', () => {
  it('clears auth cookies, localStorage client id, and resets state', async () => {
    setCookie('access_token', 'tok')
    setCookie('refresh_token', 'r')
    setCookie('session_id', 's')
    localStorage.setItem('oauth_client_id', 'client-1')
    mockFetch(async () =>
      mockResponse({
        json: {
          user_id: 1,
          name: 'A',
          email: 'a@x',
          user_type: 'human',
          scopes: [],
        },
      }),
    )
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true))

    await act(async () => {
      await result.current.logout()
    })
    expect(result.current.isAuthenticated).toBe(false)
    expect(result.current.user).toBeNull()
    expect(localStorage.getItem('oauth_client_id')).toBeNull()
    expect(document.cookie).not.toContain('access_token=tok')
  })

  it('still clears state when the logout request throws', async () => {
    setCookie('access_token', 'tok')
    let meDone = false
    mockFetch(async (input) => {
      const url = input.toString()
      if (url.includes('/auth/logout')) throw new Error('boom')
      meDone = true
      return mockResponse({
        json: {
          user_id: 1,
          name: 'A',
          email: 'a@x',
          user_type: 'human',
          scopes: [],
        },
      })
    })
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(meDone).toBe(true))
    await act(async () => {
      await result.current.logout()
    })
    expect(result.current.isAuthenticated).toBe(false)
  })
})

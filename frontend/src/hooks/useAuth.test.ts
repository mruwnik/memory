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
          modules: ['teams'],
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
      modules: ['teams'],
    })
    expect(result.current.isLoading).toBe(false)
  })

  it('defaults scopes and modules to [] when /auth/me omits them', async () => {
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
    expect(result.current.user?.modules).toEqual([])
  })

  it('drops the tokens when /auth/me is 401 and there is no refresh token', async () => {
    setCookie('access_token', 'tok')
    mockFetch(async () => mockResponse({ status: 401 }))
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.isAuthenticated).toBe(false)
    expect(document.cookie).not.toContain('access_token=tok')
  })

  it('stays unauthenticated and stops loading when apiCall throws (session cookie but no access token)', async () => {
    // The catch branch must clear isLoading so the app doesn't hang on a loading
    // spinner forever after an auth failure.
    setCookie('session_id', 'sess')
    const fetchMock = mockFetch()
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.isAuthenticated).toBe(false))
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each([
    ['a network error', async () => {
      throw new TypeError('Failed to fetch')
    }],
    ['a 500', async () => mockResponse({ status: 500 })],
  ])('keeps the tokens when /auth/me fails with %s', async (_, impl) => {
    // Every tab shares these tokens; only a 401 says the session is gone.
    setCookie('access_token', 'tok')
    const fetchMock = mockFetch(impl)
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.isAuthenticated).toBe(false)
    expect(fetchMock.mock.calls.map(([u]) => u.toString())).toEqual(['/auth/me'])
    expect(document.cookie).toContain('access_token=tok')
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
    const fetchMock = mockFetch(async () => mockResponse({ status: 401 }))
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

describe('token refresh', () => {
  const seedRefreshable = () => {
    setCookie('access_token', 'old')
    setCookie('refresh_token', 'r1')
    localStorage.setItem('oauth_client_id', 'client-1')
  }
  const callsTo = (fetchMock: ReturnType<typeof mockFetch>, path: string) =>
    fetchMock.mock.calls.filter(([u]) => u.toString().endsWith(path))
  const bearer = (init?: RequestInit) =>
    (init?.headers as Record<string, string> | undefined)?.Authorization
  const me = { user_id: 1, name: 'A', email: 'a@x', user_type: 'human', scopes: [] }

  it('posts a form-encoded refresh_token grant and stores the rotated pair', async () => {
    seedRefreshable()
    const fetchMock = mockFetch(async (input, init) => {
      if (input.toString().endsWith('/token')) {
        return mockResponse({ json: { access_token: 'new', refresh_token: 'r2' } })
      }
      return mockResponse({ status: bearer(init) === 'Bearer new' ? 200 : 401, json: me })
    })
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true))

    const [, init] = callsTo(fetchMock, '/token')[0]
    expect((init!.headers as Record<string, string>)['Content-Type']).toBe(
      'application/x-www-form-urlencoded',
    )
    expect(Object.fromEntries(new URLSearchParams(init!.body as URLSearchParams))).toEqual({
      grant_type: 'refresh_token',
      refresh_token: 'r1',
      client_id: 'client-1',
    })
    expect(document.cookie).toContain('access_token=new')
    expect(document.cookie).toContain('session_id=new')
    expect(document.cookie).toContain('refresh_token=r2')
  })

  it.each([400, 401])(
    'gives up after one refresh attempt when /token answers %i',
    async (status) => {
      seedRefreshable()
      const fetchMock = mockFetch(async (input) =>
        mockResponse({ status: input.toString().endsWith('/token') ? status : 401 }),
      )
      const { result } = renderHook(() => useAuth())
      await waitFor(() => expect(result.current.isLoading).toBe(false))

      expect(result.current.isAuthenticated).toBe(false)
      expect(callsTo(fetchMock, '/token')).toHaveLength(1)
      expect(document.cookie).not.toContain('refresh_token=r1')
      await expect(result.current.apiCall('/foo')).rejects.toThrow('No access token available')
      expect(callsTo(fetchMock, '/token')).toHaveLength(1)
    },
  )

  it('shares one refresh between concurrent 401s', async () => {
    seedRefreshable()
    let releaseToken!: () => void
    const tokenGate = new Promise<void>((resolve) => {
      releaseToken = resolve
    })
    const fetchMock = mockFetch(async (input, init) => {
      if (input.toString().endsWith('/token')) {
        await tokenGate
        return mockResponse({ json: { access_token: 'new', refresh_token: 'r2' } })
      }
      return mockResponse({ status: bearer(init) === 'Bearer new' ? 200 : 401, json: me })
    })
    const { result } = renderHook(() => useAuth())

    let statuses!: number[]
    await act(async () => {
      const calls = Promise.all([result.current.apiCall('/a'), result.current.apiCall('/b')])
      await new Promise((resolve) => setTimeout(resolve, 0))
      releaseToken()
      statuses = (await calls).map((r) => r.status)
    })

    expect(statuses).toEqual([200, 200])
    expect(callsTo(fetchMock, '/token')).toHaveLength(1)
  })

  it('reuses tokens another tab rotated instead of replaying the stale refresh token', async () => {
    seedRefreshable()
    const fetchMock = mockFetch(async (_, init) => {
      if (bearer(init) === 'Bearer old') {
        // Another tab refreshed while this request was in flight.
        setCookie('access_token', 'from-other-tab')
        return mockResponse({ status: 401 })
      }
      return mockResponse({ json: me })
    })
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true))

    expect(callsTo(fetchMock, '/token')).toHaveLength(0)
    expect(bearer(callsTo(fetchMock, '/auth/me').slice(-1)[0][1])).toBe('Bearer from-other-tab')
  })

  it('signs out every mounted useAuth when the refresh fails', async () => {
    seedRefreshable()
    let expired = false
    mockFetch(async (input) => {
      if (input.toString().endsWith('/token')) return mockResponse({ status: 400 })
      return mockResponse({ status: expired ? 401 : 200, json: me })
    })
    const first = renderHook(() => useAuth())
    const second = renderHook(() => useAuth())
    await waitFor(() => expect(second.result.current.isAuthenticated).toBe(true))
    await waitFor(() => expect(first.result.current.isAuthenticated).toBe(true))

    expired = true
    await act(async () => {
      await first.result.current.apiCall('/foo')
    })

    expect(first.result.current.isAuthenticated).toBe(false)
    expect(second.result.current.isAuthenticated).toBe(false)
    expect(second.result.current.user).toBeNull()
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

  it('does not refresh when the server rejects the logout', async () => {
    setCookie('access_token', 'tok')
    setCookie('refresh_token', 'r')
    localStorage.setItem('oauth_client_id', 'client-1')
    const fetchMock = mockFetch(async () => mockResponse({ status: 401 }))
    const { result } = renderHook(() => useAuth())
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    fetchMock.mockClear()
    setCookie('access_token', 'tok')
    setCookie('refresh_token', 'r')

    await act(async () => {
      await result.current.logout()
    })

    expect(fetchMock.mock.calls.map(([u]) => u.toString())).toEqual(['/auth/logout'])
    expect(document.cookie).not.toContain('refresh_token=r')
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

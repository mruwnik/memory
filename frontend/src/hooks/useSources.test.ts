import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useSources } from './useSources'
import { mockFetch, mockResponse, clearCookies } from '@/test/utils'

beforeEach(() => {
  clearCookies()
  document.cookie = 'access_token=tok;path=/'
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

const okJson = (json: unknown) => async () => mockResponse({ json })

/** Capture the URL + init of the last fetch call matching a substring. */
const lastCall = (fetchMock: ReturnType<typeof mockFetch>, sub: string) =>
  fetchMock.mock.calls.filter(([u]) => u.toString().includes(sub)).at(-1)!

describe('list endpoints', () => {
  it('listEmailAccounts returns parsed JSON and omits user_id when undefined', async () => {
    const fetchMock = mockFetch(okJson([{ id: 1 }]))
    const { result } = renderHook(() => useSources())
    let out!: unknown
    await act(async () => {
      out = await result.current.listEmailAccounts()
    })
    expect(out).toEqual([{ id: 1 }])
    expect(lastCall(fetchMock, '/email-accounts')[0].toString()).not.toContain(
      'user_id',
    )
  })

  it('listEmailAccounts appends user_id when provided', async () => {
    const fetchMock = mockFetch(okJson([]))
    const { result } = renderHook(() => useSources())
    await act(async () => {
      await result.current.listEmailAccounts(42)
    })
    expect(lastCall(fetchMock, '/email-accounts')[0].toString()).toContain(
      'user_id=42',
    )
  })

  it.each([
    ['listEmailAccounts', '/email-accounts', 'Failed to fetch email accounts'],
    ['listArticleFeeds', '/article-feeds', 'Failed to fetch article feeds'],
    ['listGithubAccounts', '/github/accounts', 'Failed to fetch GitHub accounts'],
    ['listGoogleAccounts', '/google-drive/accounts', 'Failed to fetch Google accounts'],
    ['listCalendarAccounts', '/calendar-accounts', 'Failed to fetch calendar accounts'],
    ['listTranscriptAccounts', '/transcript-accounts', 'Failed to fetch transcript accounts'],
  ] as const)('%s throws on a non-ok response', async (method, _path, message) => {
    mockFetch(async () => mockResponse({ status: 500 }))
    const { result } = renderHook(() => useSources())
    await expect((result.current as any)[method]()).rejects.toThrow(message)
  })
})

describe('create endpoints surface error.detail', () => {
  it('createEmailAccount throws the server detail message', async () => {
    mockFetch(async () =>
      mockResponse({ status: 400, json: { detail: 'bad imap' } }),
    )
    const { result } = renderHook(() => useSources())
    await expect(
      result.current.createEmailAccount({
        name: 'x',
        email_address: 'a@b.c',
      } as any),
    ).rejects.toThrow('bad imap')
  })

  it('createEmailAccount falls back to a generic message when detail is absent', async () => {
    mockFetch(async () => mockResponse({ status: 400, json: {} }))
    const { result } = renderHook(() => useSources())
    await expect(
      result.current.createEmailAccount({
        name: 'x',
        email_address: 'a@b.c',
      } as any),
    ).rejects.toThrow('Failed to create email account')
  })

  it('createEmailAccount POSTs the serialized body and returns JSON', async () => {
    const fetchMock = mockFetch(okJson({ id: 9 }))
    const { result } = renderHook(() => useSources())
    let out!: unknown
    await act(async () => {
      out = await result.current.createEmailAccount({
        name: 'Acct',
        email_address: 'a@b.c',
      } as any)
    })
    expect(out).toEqual({ id: 9 })
    const [, init] = lastCall(fetchMock, '/email-accounts')
    expect((init as RequestInit).method).toBe('POST')
    expect(JSON.parse((init as RequestInit).body as string)).toMatchObject({
      name: 'Acct',
    })
  })
})

describe('update / delete / sync verbs', () => {
  it('updateEmailAccount uses PATCH at the id path', async () => {
    const fetchMock = mockFetch(okJson({ id: 3 }))
    const { result } = renderHook(() => useSources())
    await act(async () => {
      await result.current.updateEmailAccount(3, { name: 'new' })
    })
    const [url, init] = lastCall(fetchMock, '/email-accounts/3')
    expect(url.toString()).toContain('/email-accounts/3')
    expect((init as RequestInit).method).toBe('PATCH')
  })

  it('deleteEmailAccount uses DELETE and resolves void on success', async () => {
    const fetchMock = mockFetch(async () => mockResponse({ json: {} }))
    const { result } = renderHook(() => useSources())
    await act(async () => {
      await expect(
        result.current.deleteEmailAccount(5),
      ).resolves.toBeUndefined()
    })
    expect((lastCall(fetchMock, '/email-accounts/5')[1] as RequestInit).method).toBe(
      'DELETE',
    )
  })

  it('deleteEmailAccount throws on failure', async () => {
    mockFetch(async () => mockResponse({ status: 500 }))
    const { result } = renderHook(() => useSources())
    await expect(result.current.deleteEmailAccount(5)).rejects.toThrow(
      'Failed to delete email account',
    )
  })

  it('syncEmailAccount POSTs to the sync subpath', async () => {
    const fetchMock = mockFetch(okJson({ task_id: 't', status: 'queued' }))
    const { result } = renderHook(() => useSources())
    let out!: unknown
    await act(async () => {
      out = await result.current.syncEmailAccount(7)
    })
    expect(out).toEqual({ task_id: 't', status: 'queued' })
    const [url, init] = lastCall(fetchMock, '/email-accounts/7/sync')
    expect(url.toString()).toContain('/email-accounts/7/sync')
    expect((init as RequestInit).method).toBe('POST')
  })

  it('testEmailAccount returns status/message', async () => {
    mockFetch(okJson({ status: 'ok', message: 'connected' }))
    const { result } = renderHook(() => useSources())
    let out!: unknown
    await act(async () => {
      out = await result.current.testEmailAccount(1)
    })
    expect(out).toEqual({ status: 'ok', message: 'connected' })
  })
})

describe('article feeds', () => {
  it('discoverFeed encodes the url query parameter', async () => {
    const fetchMock = mockFetch(okJson({ url: 'x', title: null, description: null }))
    const { result } = renderHook(() => useSources())
    await act(async () => {
      await result.current.discoverFeed('https://a.com/feed?x=1')
    })
    const url = lastCall(fetchMock, '/article-feeds/discover')[0].toString()
    expect(url).toContain(encodeURIComponent('https://a.com/feed?x=1'))
  })
})

describe('github repos and projects', () => {
  it('syncGithubRepo includes the force_full query flag', async () => {
    const fetchMock = mockFetch(okJson({ task_id: 't', status: 's' }))
    const { result } = renderHook(() => useSources())
    await act(async () => {
      await result.current.syncGithubRepo(1, 2, true)
    })
    expect(
      lastCall(fetchMock, '/github/accounts/1/repos/2/sync')[0].toString(),
    ).toContain('force_full=true')
  })

  it('listGithubProjects omits the query string when no filters are set', async () => {
    const fetchMock = mockFetch(okJson([]))
    const { result } = renderHook(() => useSources())
    await act(async () => {
      await result.current.listGithubProjects()
    })
    expect(lastCall(fetchMock, '/github/projects')[0].toString()).not.toContain(
      '?',
    )
  })

  it('listGithubProjects builds owner + include_closed query params', async () => {
    const fetchMock = mockFetch(okJson([]))
    const { result } = renderHook(() => useSources())
    await act(async () => {
      await result.current.listGithubProjects('me', true)
    })
    const url = lastCall(fetchMock, '/github/projects')[0].toString()
    expect(url).toContain('owner=me')
    expect(url).toContain('include_closed=true')
  })

  it('listAvailableProjects surfaces error.detail on failure', async () => {
    mockFetch(async () =>
      mockResponse({ status: 400, json: { detail: 'no access' } }),
    )
    const { result } = renderHook(() => useSources())
    await expect(
      result.current.listAvailableProjects(1, 'org'),
    ).rejects.toThrow('no access')
  })

  it('syncGithubProjects encodes owner/is_org/include_closed', async () => {
    const fetchMock = mockFetch(okJson({ task_id: 't', status: 's' }))
    const { result } = renderHook(() => useSources())
    await act(async () => {
      await result.current.syncGithubProjects('org', false, true)
    })
    const url = lastCall(fetchMock, '/github/projects/sync')[0].toString()
    expect(url).toContain('owner=org')
    expect(url).toContain('is_org=false')
    expect(url).toContain('include_closed=true')
  })
})

describe('google drive', () => {
  it('getGoogleAvailableScopes unwraps the scopes field', async () => {
    mockFetch(okJson({ scopes: { drive: { scope: 's', label: 'l', description: 'd' } } }))
    const { result } = renderHook(() => useSources())
    let out!: unknown
    await act(async () => {
      out = await result.current.getGoogleAvailableScopes()
    })
    expect(out).toEqual({ drive: { scope: 's', label: 'l', description: 'd' } })
  })

  it('getGoogleAuthUrl encodes multiple scopes', async () => {
    const fetchMock = mockFetch(okJson({ authorization_url: 'https://g' }))
    const { result } = renderHook(() => useSources())
    await act(async () => {
      await result.current.getGoogleAuthUrl(['a/b', 'c/d'])
    })
    const url = lastCall(fetchMock, '/google-drive/authorize')[0].toString()
    expect(url).toContain('scopes=' + encodeURIComponent('a/b'))
    expect(url).toContain('scopes=' + encodeURIComponent('c/d'))
  })

  it('getGoogleAuthUrl omits the query when no scopes are passed', async () => {
    const fetchMock = mockFetch(okJson({ authorization_url: 'https://g' }))
    const { result } = renderHook(() => useSources())
    await act(async () => {
      await result.current.getGoogleAuthUrl()
    })
    expect(
      lastCall(fetchMock, '/google-drive/authorize')[0].toString(),
    ).not.toContain('scopes=')
  })

  it('browseGoogleDrive adds page_token only when provided', async () => {
    const fetchMock = mockFetch(okJson({ items: [] }))
    const { result } = renderHook(() => useSources())
    await act(async () => {
      await result.current.browseGoogleDrive(1, 'fid', 'pt')
    })
    const url = lastCall(fetchMock, '/google-drive/accounts/1/browse')[0].toString()
    expect(url).toContain('folder_id=fid')
    expect(url).toContain('page_token=pt')
  })

  it('getGoogleOAuthConfig returns null on a 404', async () => {
    mockFetch(async () => mockResponse({ status: 404 }))
    const { result } = renderHook(() => useSources())
    let out!: unknown
    await act(async () => {
      out = await result.current.getGoogleOAuthConfig()
    })
    expect(out).toBeNull()
  })

  it('getGoogleOAuthConfig throws on other errors', async () => {
    mockFetch(async () => mockResponse({ status: 500 }))
    const { result } = renderHook(() => useSources())
    await expect(result.current.getGoogleOAuthConfig()).rejects.toThrow(
      'Failed to fetch Google OAuth config',
    )
  })

  it('uploadGoogleOAuthConfig posts FormData with a bearer token from the cookie', async () => {
    const fetchMock = mockFetch(okJson({ id: 1 }))
    const { result } = renderHook(() => useSources())
    const file = new File(['{}'], 'creds.json', { type: 'application/json' })
    let out!: unknown
    await act(async () => {
      out = await result.current.uploadGoogleOAuthConfig(file)
    })
    expect(out).toEqual({ id: 1 })
    const [url, init] = lastCall(fetchMock, '/google-drive/config')
    expect(url.toString()).toContain('/google-drive/config')
    expect((init as RequestInit).body).toBeInstanceOf(FormData)
    const headers = (init as RequestInit).headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer tok')
  })

  it('uploadGoogleOAuthConfig throws error.detail on failure', async () => {
    mockFetch(async () => mockResponse({ status: 400, json: { detail: 'bad file' } }))
    const { result } = renderHook(() => useSources())
    const file = new File(['{}'], 'creds.json')
    await expect(
      result.current.uploadGoogleOAuthConfig(file),
    ).rejects.toThrow('bad file')
  })

  it('reauthorizeGoogleAccount posts scopes in the body', async () => {
    const fetchMock = mockFetch(okJson({ authorization_url: 'u' }))
    const { result } = renderHook(() => useSources())
    await act(async () => {
      await result.current.reauthorizeGoogleAccount(3, ['s1'])
    })
    const [, init] = lastCall(fetchMock, '/google-drive/accounts/3/reauthorize')
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      scopes: ['s1'],
    })
  })
})

describe('calendar and transcript verbs', () => {
  it('syncCalendarAccount passes force_full', async () => {
    const fetchMock = mockFetch(okJson({ task_id: 't', status: 's' }))
    const { result } = renderHook(() => useSources())
    await act(async () => {
      await result.current.syncCalendarAccount(2, true)
    })
    expect(
      lastCall(fetchMock, '/calendar-accounts/2/sync')[0].toString(),
    ).toContain('force_full=true')
  })

  it('listTranscriptProviders returns the provider list', async () => {
    mockFetch(okJson(['fireflies', 'otter']))
    const { result } = renderHook(() => useSources())
    let out!: unknown
    await act(async () => {
      out = await result.current.listTranscriptProviders()
    })
    expect(out).toEqual(['fireflies', 'otter'])
  })

  it('rescanTranscriptAccount POSTs to the rescan subpath', async () => {
    const fetchMock = mockFetch(okJson({ task_id: 't', status: 's' }))
    const { result } = renderHook(() => useSources())
    await act(async () => {
      await result.current.rescanTranscriptAccount(4)
    })
    const [url, init] = lastCall(fetchMock, '/transcript-accounts/4/rescan')
    expect(url.toString()).toContain('/transcript-accounts/4/rescan')
    expect((init as RequestInit).method).toBe('POST')
  })
})

describe('photos', () => {
  it('deletePhoto DELETEs the photo and resolves void', async () => {
    const fetchMock = mockFetch(async () => mockResponse({ json: {} }))
    const { result } = renderHook(() => useSources())
    await act(async () => {
      await expect(result.current.deletePhoto(11)).resolves.toBeUndefined()
    })
    expect((lastCall(fetchMock, '/photos/11')[1] as RequestInit).method).toBe(
      'DELETE',
    )
  })

  it('deletePhoto throws on failure', async () => {
    mockFetch(async () => mockResponse({ status: 500 }))
    const { result } = renderHook(() => useSources())
    await expect(result.current.deletePhoto(11)).rejects.toThrow(
      'Failed to delete photo',
    )
  })
})

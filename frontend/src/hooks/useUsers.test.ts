import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useUsers } from './useUsers'
import { mockFetch, mockResponse, setAuthCookies, clearCookies } from '@/test/utils'

const setup = () => renderHook(() => useUsers()).result.current

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

const user = (over: Record<string, any> = {}) => ({
  id: 1,
  name: 'Alice',
  email: 'a@x.com',
  user_type: 'human',
  scopes: [],
  api_key_count: 0,
  ...over,
})

// Route fetch by an exact path match against the endpoint portion of the URL.
const routeByPath = (table: Record<string, () => Response>) =>
  mockFetch(async (input) => {
    const url = String(input)
    const path = url.replace(/^.*?(\/[^?]*).*$/, '$1')
    const handler = table[path]
    return handler ? handler() : mockResponse({ status: 404, json: { detail: 'not found' } })
  })

describe('useUsers.listUsers', () => {
  it('returns the user list on success', async () => {
    const users = [user(), user({ id: 2 })]
    const fetchMock = routeByPath({ '/users': () => mockResponse({ json: users }) })
    const { listUsers } = setup()

    expect(await listUsers()).toEqual(users)
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/users'))).toBe(true)
  })

  it.each([
    [403, 'Insufficient permissions'],
    [500, 'Failed to fetch users'],
  ])('throws %s -> %s', async (status, message) => {
    routeByPath({ '/users': () => mockResponse({ status, json: {} }) })
    const { listUsers } = setup()
    await expect(listUsers()).rejects.toThrow(message)
  })
})

describe('useUsers.listScopes', () => {
  it('returns scopes on success', async () => {
    const scopes = [{ value: 'teams', label: 'Teams', description: 'd', category: 'c' }]
    routeByPath({ '/users/scopes': () => mockResponse({ json: scopes }) })
    const { listScopes } = setup()
    expect(await listScopes()).toEqual(scopes)
  })

  it.each([
    [403, 'Insufficient permissions'],
    [500, 'Failed to fetch available scopes'],
  ])('throws %s -> %s', async (status, message) => {
    routeByPath({ '/users/scopes': () => mockResponse({ status, json: {} }) })
    const { listScopes } = setup()
    await expect(listScopes()).rejects.toThrow(message)
  })
})

describe('useUsers.getUser', () => {
  it('fetches /users/:id and returns the user', async () => {
    const fetchMock = routeByPath({ '/users/3': () => mockResponse({ json: user({ id: 3 }) }) })
    const { getUser } = setup()

    expect(await getUser(3)).toEqual(user({ id: 3 }))
    expect(fetchMock.mock.calls.some((c) => String(c[0]).endsWith('/users/3'))).toBe(true)
  })

  it.each([
    [403, 'Insufficient permissions'],
    [404, 'User not found'],
    [500, 'Failed to fetch user'],
  ])('throws %s -> %s', async (status, message) => {
    routeByPath({ '/users/3': () => mockResponse({ status, json: {} }) })
    const { getUser } = setup()
    await expect(getUser(3)).rejects.toThrow(message)
  })
})

describe('useUsers.getCurrentUser', () => {
  it('fetches /users/me and returns the user', async () => {
    routeByPath({ '/users/me': () => mockResponse({ json: user() }) })
    const { getCurrentUser } = setup()
    expect(await getCurrentUser()).toEqual(user())
  })

  it('throws on failure', async () => {
    routeByPath({ '/users/me': () => mockResponse({ status: 500, json: {} }) })
    const { getCurrentUser } = setup()
    await expect(getCurrentUser()).rejects.toThrow('Failed to fetch current user')
  })
})

describe('useUsers.createUser', () => {
  it('POSTs the payload and returns the created user', async () => {
    const created = user({ id: 9, name: 'New' })
    const fetchMock = routeByPath({ '/users': () => mockResponse({ status: 201, json: created }) })
    const { createUser } = setup()

    const out = await createUser({ name: 'New', email: 'n@x.com', password: 'pw' })

    expect(out).toEqual(created)
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/users'))
    expect(call?.[1]?.method).toBe('POST')
    expect(JSON.parse(call?.[1]?.body as string)).toEqual({ name: 'New', email: 'n@x.com', password: 'pw' })
  })

  it('throws the server detail on failure', async () => {
    routeByPath({ '/users': () => mockResponse({ status: 400, json: { detail: 'email taken' }, ok: false }) })
    const { createUser } = setup()
    await expect(createUser({ name: 'N', email: 'e' })).rejects.toThrow('email taken')
  })

  it('throws a generic message when no detail is present', async () => {
    routeByPath({ '/users': () => mockResponse({ status: 400, json: {}, ok: false }) })
    const { createUser } = setup()
    await expect(createUser({ name: 'N', email: 'e' })).rejects.toThrow('Failed to create user')
  })
})

describe('useUsers.updateUser', () => {
  it('PATCHes /users/:id with the payload', async () => {
    const updated = user({ id: 4, name: 'Renamed' })
    const fetchMock = routeByPath({ '/users/4': () => mockResponse({ json: updated }) })
    const { updateUser } = setup()

    const out = await updateUser(4, { name: 'Renamed' })

    expect(out).toEqual(updated)
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/users/4'))
    expect(call?.[1]?.method).toBe('PATCH')
    expect(JSON.parse(call?.[1]?.body as string)).toEqual({ name: 'Renamed' })
  })

  it('throws the server detail on failure', async () => {
    routeByPath({ '/users/4': () => mockResponse({ status: 409, json: { detail: 'conflict' }, ok: false }) })
    const { updateUser } = setup()
    await expect(updateUser(4, { name: 'x' })).rejects.toThrow('conflict')
  })
})

describe('useUsers.deleteUser', () => {
  it('DELETEs /users/:id and resolves on success', async () => {
    const fetchMock = routeByPath({ '/users/4': () => mockResponse({ status: 204, json: {} }) })
    const { deleteUser } = setup()

    await expect(deleteUser(4)).resolves.toBeUndefined()
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/users/4'))
    expect(call?.[1]?.method).toBe('DELETE')
  })

  it('throws the server detail on failure', async () => {
    routeByPath({ '/users/4': () => mockResponse({ status: 403, json: { detail: 'forbidden' }, ok: false }) })
    const { deleteUser } = setup()
    await expect(deleteUser(4)).rejects.toThrow('forbidden')
  })
})

describe('useUsers.regenerateApiKey', () => {
  it('POSTs to /users/:id/api-keys and returns the key', async () => {
    const fetchMock = routeByPath({ '/users/4/api-keys': () => mockResponse({ json: { key: 'mcp_abc' } }) })
    const { regenerateApiKey } = setup()

    const out = await regenerateApiKey(4)

    expect(out).toEqual({ key: 'mcp_abc' })
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/users/4/api-keys'))
    expect(call?.[1]?.method).toBe('POST')
    expect(JSON.parse(call?.[1]?.body as string)).toEqual({ name: 'Default API Key' })
  })

  it('throws the server detail on failure', async () => {
    routeByPath({ '/users/4/api-keys': () => mockResponse({ status: 500, json: { detail: 'boom' }, ok: false }) })
    const { regenerateApiKey } = setup()
    await expect(regenerateApiKey(4)).rejects.toThrow('boom')
  })
})

describe('useUsers.changePassword', () => {
  it('POSTs to /users/me/change-password and resolves on success', async () => {
    const fetchMock = routeByPath({ '/users/me/change-password': () => mockResponse({ json: {} }) })
    const { changePassword } = setup()

    await expect(
      changePassword({ current_password: 'old', new_password: 'new' }),
    ).resolves.toBeUndefined()
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/change-password'))
    expect(call?.[1]?.method).toBe('POST')
    expect(JSON.parse(call?.[1]?.body as string)).toEqual({ current_password: 'old', new_password: 'new' })
  })

  it('throws the server detail on failure', async () => {
    routeByPath({
      '/users/me/change-password': () => mockResponse({ status: 400, json: { detail: 'wrong password' }, ok: false }),
    })
    const { changePassword } = setup()
    await expect(changePassword({ current_password: 'x', new_password: 'y' })).rejects.toThrow('wrong password')
  })
})

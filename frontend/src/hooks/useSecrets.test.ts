import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useSecrets } from './useSecrets'
import {
  mockFetch,
  mockFetchRoutes,
  mockResponse,
  setAuthCookies,
  clearCookies,
} from '@/test/utils'

const secret = {
  id: 1,
  name: 'API_KEY',
  description: 'a key',
  created_at: '2024-01-01',
  updated_at: '2024-01-02',
}

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('useSecrets.listSecrets', () => {
  it('returns the parsed list on success', async () => {
    const fetchMock = mockFetchRoutes({
      '/auth/me': { json: {} },
      '/secrets': { json: [secret] },
    })
    const { result } = renderHook(() => useSecrets())
    const secrets = await result.current.listSecrets()
    expect(secrets).toEqual([secret])
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/secrets'))
    expect(call).toBeDefined()
    expect(call?.[1]?.method ?? 'GET').toBe('GET')
  })

  it('throws with detail from error body on failure', async () => {
    mockFetchRoutes({
      '/auth/me': { json: {} },
      '/secrets': { status: 500, json: { detail: 'boom' } },
    })
    const { result } = renderHook(() => useSecrets())
    await expect(result.current.listSecrets()).rejects.toThrow('boom')
  })

  it('throws a default message when error body has no detail', async () => {
    mockFetchRoutes({
      '/auth/me': { json: {} },
      '/secrets': { status: 500, json: {} },
    })
    const { result } = renderHook(() => useSecrets())
    await expect(result.current.listSecrets()).rejects.toThrow('Failed to list secrets')
  })
})

describe('useSecrets.createSecret', () => {
  it('POSTs the serialized body and returns created secret', async () => {
    const fetchMock = mockFetchRoutes({
      '/auth/me': { json: {} },
      '/secrets': { json: secret },
    })
    const { result } = renderHook(() => useSecrets())
    const payload = { name: 'API_KEY', value: 'v', description: 'd' }
    const created = await result.current.createSecret(payload)
    expect(created).toEqual(secret)
    const call = fetchMock.mock.calls.find(
      (c) => String(c[0]).endsWith('/secrets') && c[1]?.method === 'POST',
    )
    expect(call).toBeDefined()
    expect(JSON.parse(call?.[1]?.body as string)).toEqual(payload)
  })

  it('throws with detail on failure', async () => {
    mockFetch(async (input, init) => {
      if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
      if (init?.method === 'POST') return mockResponse({ status: 400, json: { detail: 'dup' } })
      return mockResponse({ json: {} })
    })
    const { result } = renderHook(() => useSecrets())
    await expect(
      result.current.createSecret({ name: 'x', value: 'y' }),
    ).rejects.toThrow('dup')
  })
})

describe('useSecrets.updateSecret', () => {
  it('PATCHes the right URL with serialized body', async () => {
    const fetchMock = mockFetchRoutes({
      '/auth/me': { json: {} },
      '/secrets/1': { json: secret },
    })
    const { result } = renderHook(() => useSecrets())
    const updated = await result.current.updateSecret(1, { value: 'new' })
    expect(updated).toEqual(secret)
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/secrets/1'))
    expect(call?.[1]?.method).toBe('PATCH')
    expect(JSON.parse(call?.[1]?.body as string)).toEqual({ value: 'new' })
  })

  it('throws with detail on failure', async () => {
    mockFetchRoutes({
      '/auth/me': { json: {} },
      '/secrets/1': { status: 404, json: { detail: 'missing' } },
    })
    const { result } = renderHook(() => useSecrets())
    await expect(result.current.updateSecret(1, { value: 'x' })).rejects.toThrow('missing')
  })
})

describe('useSecrets.deleteSecret', () => {
  it('DELETEs and resolves to undefined', async () => {
    const fetchMock = mockFetchRoutes({
      '/auth/me': { json: {} },
      '/secrets/9': { status: 204, json: {} },
    })
    const { result } = renderHook(() => useSecrets())
    await expect(result.current.deleteSecret(9)).resolves.toBeUndefined()
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/secrets/9'))
    expect(call?.[1]?.method).toBe('DELETE')
  })

  it('throws with detail on failure', async () => {
    mockFetchRoutes({
      '/auth/me': { json: {} },
      '/secrets/9': { status: 403, json: { detail: 'no' } },
    })
    const { result } = renderHook(() => useSecrets())
    await expect(result.current.deleteSecret(9)).rejects.toThrow('no')
  })
})

describe('useSecrets.getSecretValue', () => {
  it('GETs the value endpoint and returns the secret with value', async () => {
    const withValue = { ...secret, value: 'plaintext' }
    const fetchMock = mockFetchRoutes({
      '/auth/me': { json: {} },
      '/secrets/1/value': { json: withValue },
    })
    const { result } = renderHook(() => useSecrets())
    const got = await result.current.getSecretValue(1)
    expect(got).toEqual(withValue)
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith('/secrets/1/value'))
    expect(call).toBeDefined()
  })

  it('throws with detail on failure', async () => {
    mockFetchRoutes({
      '/auth/me': { json: {} },
      '/secrets/1/value': { status: 500, json: { detail: 'fail' } },
    })
    const { result } = renderHook(() => useSecrets())
    await expect(result.current.getSecretValue(1)).rejects.toThrow('fail')
  })
})

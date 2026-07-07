import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { usePeople } from './usePeople'
import { mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import { mcpResult, mcpToolError, mcpRpcError, mcpArgsAt, mcpToolAt, mcpCalls } from './mcpEnvelope.testhelper'

const setup = () => renderHook(() => usePeople()).result.current

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('usePeople.listPeople', () => {
  it('returns the people list with default pagination params', async () => {
    const people = [{ id: 1, identifier: 'alice', display_name: 'Alice' }]
    const fetchMock = mockFetchRoutes({ people_list_all: mcpResult(people) })
    const { listPeople } = setup()

    const out = await listPeople()

    expect(out).toEqual(people)
    const args = mcpArgsAt(fetchMock)
    expect(args).toMatchObject({ limit: 50, offset: 0 })
    expect(args.tags).toBeUndefined()
    expect(args.search).toBeUndefined()
    expect(mcpToolAt(fetchMock)).toBe('people_list_all')
    expect(mcpCalls(fetchMock).at(-1)?.[1].method).toBe('POST')
  })

  it('forwards filters (tags, search, limit, offset)', async () => {
    const fetchMock = mockFetchRoutes({ people_list_all: mcpResult([]) })
    const { listPeople } = setup()

    await listPeople({ tags: ['vip'], search: 'bob', limit: 10, offset: 20 })

    expect(mcpArgsAt(fetchMock)).toMatchObject({
      tags: ['vip'],
      search: 'bob',
      limit: 10,
      offset: 20,
    })
  })

  it.each([
    ['empty array', mcpResult([]), []],
    ['null payload', mcpResult(null), []],
  ])('returns [] for %s', async (_label, route, expected) => {
    mockFetchRoutes({ people_list_all: route })
    const { listPeople } = setup()
    expect(await listPeople()).toEqual(expected)
  })

  it('propagates an RPC error', async () => {
    mockFetchRoutes({ people_list_all: mcpRpcError('boom') })
    const { listPeople } = setup()
    await expect(listPeople()).rejects.toThrow(/boom/)
  })
})

describe('usePeople.getPerson', () => {
  it('returns the first person and sends include_tidbits default true', async () => {
    const person = { id: 2, identifier: 'bob', display_name: 'Bob' }
    const fetchMock = mockFetchRoutes({ people_fetch: mcpResult(person) })
    const { getPerson } = setup()

    const out = await getPerson('bob')

    expect(out).toEqual(person)
    expect(mcpArgsAt(fetchMock)).toEqual({ identifier: 'bob', include_tidbits: true })
  })

  it('passes include_tidbits=false when requested', async () => {
    const fetchMock = mockFetchRoutes({ people_fetch: mcpResult({ id: 1, identifier: 'x', display_name: 'X' }) })
    const { getPerson } = setup()
    await getPerson('x', false)
    expect(mcpArgsAt(fetchMock).include_tidbits).toBe(false)
  })

  it('returns null when result is null', async () => {
    mockFetchRoutes({ people_fetch: mcpResult(null) })
    const { getPerson } = setup()
    expect(await getPerson('nobody')).toBeNull()
  })
})

describe('usePeople.addPerson', () => {
  it('sends create fields and returns the success envelope', async () => {
    const resp = { success: true, person: { id: 9, identifier: 'new', display_name: 'New' } }
    const fetchMock = mockFetchRoutes({ people_upsert: mcpResult(resp) })
    const { addPerson } = setup()

    const out = await addPerson({
      identifier: 'new',
      display_name: 'New',
      aliases: ['n'],
      contact_info: { email: 'n@x.com' },
      content: 'a note',
      tidbit_type: 'fact',
      tags: ['t'],
      project_id: 3,
      sensitivity: 'public',
    })

    expect(out).toEqual(resp)
    expect(mcpArgsAt(fetchMock)).toMatchObject({
      identifier: 'new',
      display_name: 'New',
      aliases: ['n'],
      contact_info: { email: 'n@x.com' },
      content: 'a note',
      tidbit_type: 'fact',
      tags: ['t'],
      project_id: 3,
      sensitivity: 'public',
    })
  })

  it('falls back to a generic error when result is empty', async () => {
    mockFetchRoutes({ people_upsert: mcpResult(null) })
    const { addPerson } = setup()
    expect(await addPerson({ identifier: 'x', display_name: 'X' })).toEqual({
      success: false,
      error: 'Unknown error',
    })
  })
})

describe('usePeople.updatePerson', () => {
  it('sends identifier plus update fields including replace_aliases', async () => {
    const resp = { success: true }
    const fetchMock = mockFetchRoutes({ people_upsert: mcpResult(resp) })
    const { updatePerson } = setup()

    const out = await updatePerson('bob', { display_name: 'Bobby', replace_aliases: true, tags: ['z'] })

    expect(out).toEqual(resp)
    expect(mcpArgsAt(fetchMock)).toMatchObject({
      identifier: 'bob',
      display_name: 'Bobby',
      replace_aliases: true,
      tags: ['z'],
    })
  })

  it('falls back to a generic error when result is empty', async () => {
    mockFetchRoutes({ people_upsert: mcpResult(null) })
    const { updatePerson } = setup()
    expect(await updatePerson('bob', {})).toEqual({ success: false, error: 'Unknown error' })
  })
})

describe('usePeople.deletePerson', () => {
  it('returns the delete payload and sends the identifier', async () => {
    const resp = { deleted: true, identifier: 'bob', display_name: 'Bob' }
    const fetchMock = mockFetchRoutes({ people_delete: mcpResult(resp) })
    const { deletePerson } = setup()

    const out = await deletePerson('bob')

    expect(out).toEqual(resp)
    expect(mcpArgsAt(fetchMock)).toEqual({ identifier: 'bob' })
  })
})

describe('usePeople.mergePeople', () => {
  it('sends identifiers and primary_identifier, returns the envelope', async () => {
    const resp = { success: true, merged_from: ['a', 'b'] }
    const fetchMock = mockFetchRoutes({ people_merge: mcpResult(resp) })
    const { mergePeople } = setup()

    const out = await mergePeople(['a', 'b'], 'a')

    expect(out).toEqual(resp)
    expect(mcpArgsAt(fetchMock)).toEqual({ identifiers: ['a', 'b'], primary_identifier: 'a' })
  })

  it('omits primary_identifier when not supplied and falls back on empty result', async () => {
    const fetchMock = mockFetchRoutes({ people_merge: mcpResult(null) })
    const { mergePeople } = setup()

    const out = await mergePeople(['a', 'b'])

    expect(out).toEqual({ success: false, error: 'Unknown error' })
    expect(mcpArgsAt(fetchMock).primary_identifier).toBeUndefined()
  })

  it('propagates a tool error', async () => {
    mockFetchRoutes({ people_merge: mcpToolError('cannot merge') })
    const { mergePeople } = setup()
    await expect(mergePeople(['a'])).rejects.toThrow(/cannot merge/)
  })
})

import { beforeEach, describe, expect, it } from 'vitest'
import { renderHook } from '@testing-library/react'
import { clearCookies, mockFetchRoutes, setAuthCookies } from '@/test/utils'
import { EMBED_STATUSES, useIngestedItems } from './useIngestedItems'
import { mcpArgsAt, mcpResult, mcpRpcError, mcpToolAt } from './mcpEnvelope.testhelper'

const setup = () => renderHook(() => useIngestedItems()).result.current

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('listItems', () => {
  it('asks for one page of every status without totals or metadata', async () => {
    const item = { id: 1, type: 'mail_message', embed_status: 'STORED', title: 'Hi' }
    const fetchMock = mockFetchRoutes({ core_list_items: mcpResult({ items: [item], has_more: true, total: null }) })

    const page = await setup().listItems()

    expect(page).toEqual({ items: [item], hasMore: true })
    expect(mcpToolAt(fetchMock)).toBe('core_list_items')
    expect(mcpArgsAt(fetchMock)).toEqual({
      item_types: [],
      embed_statuses: [...EMBED_STATUSES],
      limit: 50,
      offset: 0,
      include_metadata: false,
      include_total: false,
    })
  })

  it.each([
    ['a trimmed search', { contains: '  invoice ' }, { contains: 'invoice' }],
    ['a connection', { itemTypes: ['mail_message'], connectionId: '7' }, { item_types: ['mail_message'], connection_id: '7' }],
    ['statuses', { statuses: ['FAILED'] as const }, { embed_statuses: ['FAILED'] }],
  ])('forwards %s', async (_label, query, expected) => {
    const fetchMock = mockFetchRoutes({ core_list_items: mcpResult({ items: [], has_more: false }) })

    await setup().listItems(query, { limit: 5, offset: 10 })

    expect(mcpArgsAt(fetchMock)).toMatchObject({ ...expected, limit: 5, offset: 10 })
  })

  it('leaves out a blank search', async () => {
    const fetchMock = mockFetchRoutes({ core_list_items: mcpResult({ items: [], has_more: false }) })

    await setup().listItems({ contains: '   ' })

    expect(mcpArgsAt(fetchMock)).not.toHaveProperty('contains')
  })

  it('propagates a tool error', async () => {
    mockFetchRoutes({ core_list_items: mcpRpcError('boom') })
    await expect(setup().listItems()).rejects.toThrow(/boom/)
  })
})

describe('countItems', () => {
  it('returns counts by type and by connection', async () => {
    const connections = [{ id: '1234567890123456789', name: 'general', count: 3 }]
    const fetchMock = mockFetchRoutes({
      core_count_items: mcpResult({ total: 3, by_modality: {}, by_type: { discord_message: 3 }, by_connection: connections }),
    })

    const counts = await setup().countItems({ itemTypes: ['discord_message'] })

    expect(counts).toEqual({ total: 3, byType: { discord_message: 3 }, byConnection: connections })
    expect(mcpToolAt(fetchMock)).toBe('core_count_items')
    expect(mcpArgsAt(fetchMock)).toEqual({ item_types: ['discord_message'], embed_statuses: [...EMBED_STATUSES] })
  })
})

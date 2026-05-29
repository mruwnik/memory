import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useScheduledTasks } from './useScheduledTasks'
import {
  mockFetch,
  mockResponse,
  MockResponseInit,
  setAuthCookies,
  clearCookies,
} from '@/test/utils'
import { mcpResult } from './mcpEnvelope.testhelper'

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

// Route /auth/me to {} and all /mcp/* calls to the supplied response.
function routeMcp(resp: MockResponseInit) {
  return mockFetch(async (input) => {
    const url = String(input)
    if (url.endsWith('/auth/me')) return mockResponse({ json: {} })
    return mockResponse(resp)
  })
}

const task = {
  id: 't1',
  user_id: 1,
  task_type: 'digest',
  topic: null,
  message: null,
  notification_channel: null,
  notification_target: null,
  data: null,
  cron_expression: '0 9 * * *',
  next_scheduled_time: null,
  enabled: true,
  created_at: null,
  updated_at: null,
}

function bodyArgs(fetchMock: ReturnType<typeof mockFetch>, methodSubstr: string) {
  const call = fetchMock.mock.calls.find((c) => String(c[0]).includes(`/mcp/${methodSubstr}`))
  return JSON.parse(call?.[1]?.body as string).params.arguments
}

describe('useScheduledTasks.listTasks', () => {
  it('returns the unwrapped task list and sends an empty args object by default', async () => {
    const fetchMock = routeMcp(mcpResult([task]))
    const { result } = renderHook(() => useScheduledTasks())
    const tasks = await result.current.listTasks()
    expect(tasks).toEqual([task])
    expect(bodyArgs(fetchMock, 'scheduler_list_all')).toEqual({})
  })

  it('only forwards provided filters', async () => {
    const fetchMock = routeMcp(mcpResult([task]))
    const { result } = renderHook(() => useScheduledTasks())
    await result.current.listTasks({ task_type: 'digest', enabled: false, limit: 5 })
    expect(bodyArgs(fetchMock, 'scheduler_list_all')).toEqual({
      task_type: 'digest',
      enabled: false,
      limit: 5,
    })
  })

  it('forwards enabled=false (the !== undefined branch)', async () => {
    const fetchMock = routeMcp(mcpResult([[]]))
    const { result } = renderHook(() => useScheduledTasks())
    await result.current.listTasks({ enabled: false })
    expect(bodyArgs(fetchMock, 'scheduler_list_all')).toEqual({ enabled: false })
  })

  it('propagates MCP transport errors', async () => {
    const fetchMock = mockFetch(async (input) => {
      if (String(input).endsWith('/auth/me')) return mockResponse({ json: {} })
      return mockResponse({ status: 500, text: 'boom' })
    })
    const { result } = renderHook(() => useScheduledTasks())
    await expect(result.current.listTasks()).rejects.toThrow('scheduler_list_all failed')
    expect(fetchMock).toHaveBeenCalled()
  })
})

describe('useScheduledTasks.updateTask', () => {
  it('upserts with task_id plus update fields and returns the task', async () => {
    const fetchMock = routeMcp(mcpResult(task))
    const { result } = renderHook(() => useScheduledTasks())
    const got = await result.current.updateTask('t1', { enabled: false, topic: 'x' })
    expect(got).toEqual(task)
    expect(bodyArgs(fetchMock, 'scheduler_upsert')).toEqual({
      task_id: 't1',
      enabled: false,
      topic: 'x',
    })
  })
})

describe('useScheduledTasks.toggleTask', () => {
  it('delegates to updateTask with only the enabled flag', async () => {
    const fetchMock = routeMcp(mcpResult(task))
    const { result } = renderHook(() => useScheduledTasks())
    const got = await result.current.toggleTask('t1', true)
    expect(got).toEqual(task)
    expect(bodyArgs(fetchMock, 'scheduler_upsert')).toEqual({ task_id: 't1', enabled: true })
  })
})

describe('useScheduledTasks.deleteTask', () => {
  it('calls scheduler_delete with the task id and resolves to undefined', async () => {
    const fetchMock = routeMcp(mcpResult({ status: 'ok' }))
    const { result } = renderHook(() => useScheduledTasks())
    await expect(result.current.deleteTask('t1')).resolves.toBeUndefined()
    expect(bodyArgs(fetchMock, 'scheduler_delete')).toEqual({ task_id: 't1' })
  })
})

describe('useScheduledTasks.getExecutions', () => {
  it('passes the default limit of 20', async () => {
    const fetchMock = routeMcp(mcpResult([{ id: 'e1' }]))
    const { result } = renderHook(() => useScheduledTasks())
    const got = await result.current.getExecutions('t1')
    expect(got).toEqual([{ id: 'e1' }])
    expect(bodyArgs(fetchMock, 'scheduler_executions')).toEqual({ task_id: 't1', limit: 20 })
  })

  it('passes a custom limit', async () => {
    const fetchMock = routeMcp(mcpResult([]))
    const { result } = renderHook(() => useScheduledTasks())
    await result.current.getExecutions('t1', 100)
    expect(bodyArgs(fetchMock, 'scheduler_executions')).toEqual({ task_id: 't1', limit: 100 })
  })
})

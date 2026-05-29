import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useTasks } from './useTasks'
import { mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import { mcpResult, mcpArgsAt, mcpUrlAt } from './mcpEnvelope.testhelper'

const setup = () => renderHook(() => useTasks()).result.current

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

const sampleTask = { id: 1, task_title: 'do', status: 'pending', tags: [] }

describe('useTasks.listTasks', () => {
  it('returns tasks with default params (include_completed false, limit 50, offset 0)', async () => {
    const fetchMock = mockFetchRoutes({ organizer_list_tasks: mcpResult([sampleTask]) })
    const { listTasks } = setup()

    const out = await listTasks()

    expect(out).toEqual([sampleTask])
    expect(mcpUrlAt(fetchMock)).toContain('/mcp/organizer_list_tasks')
    expect(mcpArgsAt(fetchMock)).toMatchObject({ include_completed: false, limit: 50, offset: 0 })
  })

  it('forwards a single status and priority', async () => {
    const fetchMock = mockFetchRoutes({ organizer_list_tasks: mcpResult([]) })
    const { listTasks } = setup()

    await listTasks({ status: 'in_progress', priority: 'high', include_completed: true, limit: 5, offset: 2 })

    expect(mcpArgsAt(fetchMock)).toMatchObject({
      status: 'in_progress',
      priority: 'high',
      include_completed: true,
      limit: 5,
      offset: 2,
    })
  })

  it('drops status when it is comma-separated (MCP accepts single values only)', async () => {
    const fetchMock = mockFetchRoutes({ organizer_list_tasks: mcpResult([]) })
    const { listTasks } = setup()

    await listTasks({ status: 'pending,in_progress' })

    expect(mcpArgsAt(fetchMock).status).toBeUndefined()
  })

  it('returns [] when result is empty', async () => {
    mockFetchRoutes({ organizer_list_tasks: mcpResult(null) })
    const { listTasks } = setup()
    expect(await listTasks()).toEqual([])
  })
})

describe('useTasks.getTask', () => {
  it('returns the task and sends task_id', async () => {
    const fetchMock = mockFetchRoutes({ organizer_get_task: mcpResult({ success: true, task: sampleTask }) })
    const { getTask } = setup()

    const out = await getTask(1)

    expect(out).toEqual(sampleTask)
    expect(mcpArgsAt(fetchMock)).toEqual({ task_id: 1 })
  })

  it('returns null when task is absent', async () => {
    mockFetchRoutes({ organizer_get_task: mcpResult({ success: true }) })
    const { getTask } = setup()
    expect(await getTask(1)).toBeNull()
  })

  it('throws when the response carries an error', async () => {
    mockFetchRoutes({ organizer_get_task: mcpResult({ error: 'not found' }) })
    const { getTask } = setup()
    await expect(getTask(99)).rejects.toThrow('not found')
  })
})

describe('useTasks.createTask', () => {
  it('maps task_title to title and forwards optional fields', async () => {
    const created = { ...sampleTask, id: 2 }
    const fetchMock = mockFetchRoutes({ organizer_create_task: mcpResult(created) })
    const { createTask } = setup()

    const out = await createTask({
      task_title: 'write tests',
      due_date: '2026-06-01',
      priority: 'urgent',
      recurrence: 'weekly',
      tags: ['x'],
    })

    expect(out).toEqual(created)
    expect(mcpArgsAt(fetchMock)).toMatchObject({
      title: 'write tests',
      due_date: '2026-06-01',
      priority: 'urgent',
      recurrence: 'weekly',
      tags: ['x'],
    })
    expect(mcpUrlAt(fetchMock)).toContain('/mcp/organizer_create_task')
  })
})

describe('useTasks.updateTask', () => {
  it('sends task_id, maps task_title to title, forwards status', async () => {
    const updated = { ...sampleTask, task_title: 'renamed' }
    const fetchMock = mockFetchRoutes({ organizer_update_task: mcpResult(updated) })
    const { updateTask } = setup()

    const out = await updateTask(1, { task_title: 'renamed', status: 'in_progress', tags: ['a'] })

    expect(out).toEqual(updated)
    expect(mcpArgsAt(fetchMock)).toMatchObject({
      task_id: 1,
      title: 'renamed',
      status: 'in_progress',
      tags: ['a'],
    })
  })
})

describe('useTasks.completeTask', () => {
  it("updates the task with status 'done'", async () => {
    const done = { ...sampleTask, status: 'done' }
    const fetchMock = mockFetchRoutes({ organizer_update_task: mcpResult(done) })
    const { completeTask } = setup()

    const out = await completeTask(1)

    expect(out).toEqual(done)
    expect(mcpArgsAt(fetchMock)).toEqual({ task_id: 1, status: 'done' })
  })
})

describe('useTasks.deleteTask', () => {
  it("cancels the task and returns { deleted: true }", async () => {
    const fetchMock = mockFetchRoutes({ organizer_update_task: mcpResult({ ...sampleTask, status: 'cancelled' }) })
    const { deleteTask } = setup()

    const out = await deleteTask(1)

    expect(out).toEqual({ deleted: true })
    expect(mcpArgsAt(fetchMock)).toEqual({ task_id: 1, status: 'cancelled' })
    expect(mcpUrlAt(fetchMock)).toContain('/mcp/organizer_update_task')
  })
})

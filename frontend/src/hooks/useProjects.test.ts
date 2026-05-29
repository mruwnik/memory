import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useProjects } from './useProjects'
import { mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import { mcpResult, mcpArgsAt, mcpUrlAt } from './mcpEnvelope.testhelper'

const setup = () => renderHook(() => useProjects()).result.current

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('useProjects.listProjects', () => {
  it('returns projects and defaults include_teams to false', async () => {
    const projects = [{ id: 1, title: 'P1', state: 'open' }]
    const fetchMock = mockFetchRoutes({ projects_list_all: mcpResult({ projects, count: 1 }) })
    const { listProjects } = setup()

    const out = await listProjects()

    expect(out).toEqual(projects)
    expect(mcpUrlAt(fetchMock)).toContain('/mcp/projects_list_all')
    expect(mcpArgsAt(fetchMock)).toMatchObject({ include_teams: false })
  })

  it('forwards state, parent_id, search, pagination and include_teams', async () => {
    const fetchMock = mockFetchRoutes({ projects_list_all: mcpResult({ projects: [], count: 0 }) })
    const { listProjects } = setup()

    await listProjects({ state: 'closed', parent_id: 0, include_teams: true, limit: 5, offset: 10, search: 'q' })

    expect(mcpArgsAt(fetchMock)).toMatchObject({
      state: 'closed',
      parent_id: 0,
      include_teams: true,
      limit: 5,
      offset: 10,
      search: 'q',
    })
  })

  it('returns [] when projects field missing', async () => {
    mockFetchRoutes({ projects_list_all: mcpResult({ count: 0 }) })
    const { listProjects } = setup()
    expect(await listProjects()).toEqual([])
  })

  it('throws when the result carries an error', async () => {
    mockFetchRoutes({ projects_list_all: mcpResult({ error: 'denied' }) })
    const { listProjects } = setup()
    await expect(listProjects()).rejects.toThrow('denied')
  })
})

describe('useProjects.getProjectTree', () => {
  it('requests as_tree and returns the tree', async () => {
    const tree = [{ id: 1, title: 'root', children: [] }]
    const fetchMock = mockFetchRoutes({ projects_list_all: mcpResult({ tree, count: 1 }) })
    const { getProjectTree } = setup()

    const out = await getProjectTree({ state: 'open' })

    expect(out).toEqual(tree)
    expect(mcpArgsAt(fetchMock)).toMatchObject({ as_tree: true, state: 'open' })
  })

  it('returns [] when tree missing', async () => {
    mockFetchRoutes({ projects_list_all: mcpResult({ count: 0 }) })
    const { getProjectTree } = setup()
    expect(await getProjectTree()).toEqual([])
  })

  it('throws on error', async () => {
    mockFetchRoutes({ projects_list_all: mcpResult({ error: 'nope' }) })
    const { getProjectTree } = setup()
    await expect(getProjectTree()).rejects.toThrow('nope')
  })
})

describe('useProjects.getProject', () => {
  it('returns project and journal entries, forwarding options', async () => {
    const project = { id: 7, title: 'X', state: 'open' }
    const journal = [{ id: 1, content: 'note' }]
    const fetchMock = mockFetchRoutes({
      'auth/me': { json: { user_id: 1, scopes: ['*'] } },
      projects_fetch: mcpResult({ project, journal_entries: journal }),
    })
    const { getProject } = setup()

    const out = await getProject(7, { includeTeams: true, includeJournal: true })

    expect(out).toEqual({ project, journal_entries: journal })
    expect(mcpArgsAt(fetchMock)).toEqual({
      project_id: 7,
      include_teams: true,
      include_journal: true,
    })
  })

  it('defaults include flags to false', async () => {
    const fetchMock = mockFetchRoutes({ projects_fetch: mcpResult({ project: null }) })
    const { getProject } = setup()
    await getProject(1)
    expect(mcpArgsAt(fetchMock)).toMatchObject({ include_teams: false, include_journal: false })
  })

  it('returns { project: null } on error', async () => {
    mockFetchRoutes({ projects_fetch: mcpResult({ error: 'forbidden' }) })
    const { getProject } = setup()
    expect(await getProject(1)).toEqual({ project: null })
  })
})

describe('useProjects.createProject', () => {
  it('sends create fields, defaults state to open, returns success', async () => {
    const project = { id: 5, title: 'New', state: 'open' }
    const fetchMock = mockFetchRoutes({ projects_upsert: mcpResult({ project }) })
    const { createProject } = setup()

    const out = await createProject({ title: 'New', team_ids: [1, 2] })

    expect(out).toEqual({ success: true, project })
    expect(mcpArgsAt(fetchMock)).toMatchObject({ title: 'New', team_ids: [1, 2], state: 'open' })
  })

  it('passes explicit state through', async () => {
    const fetchMock = mockFetchRoutes({ projects_upsert: mcpResult({ project: { id: 1 } }) })
    const { createProject } = setup()
    await createProject({ title: 'T', team_ids: [1], state: 'closed', owner_id: 9, due_on: '2026-01-01' })
    expect(mcpArgsAt(fetchMock)).toMatchObject({ state: 'closed', owner_id: 9, due_on: '2026-01-01' })
  })

  it('returns failure when the result has an error', async () => {
    mockFetchRoutes({ projects_upsert: mcpResult({ error: 'bad' }) })
    const { createProject } = setup()
    expect(await createProject({ title: 'T', team_ids: [1] })).toEqual({ success: false, error: 'bad' })
  })
})

describe('useProjects.updateProject', () => {
  it('sends project_id and update fields, returns success', async () => {
    const project = { id: 3, title: 'Renamed' }
    const fetchMock = mockFetchRoutes({ projects_upsert: mcpResult({ project }) })
    const { updateProject } = setup()

    const out = await updateProject(3, { title: 'Renamed', clear_parent: true, clear_due_on: true })

    expect(out).toEqual({ success: true, project })
    expect(mcpArgsAt(fetchMock)).toMatchObject({
      project_id: 3,
      title: 'Renamed',
      clear_parent: true,
      clear_due_on: true,
    })
  })

  it('returns failure on error', async () => {
    mockFetchRoutes({ projects_upsert: mcpResult({ error: 'conflict' }) })
    const { updateProject } = setup()
    expect(await updateProject(1, { title: 'x' })).toEqual({ success: false, error: 'conflict' })
  })
})

describe('useProjects.deleteProject', () => {
  it('returns success with deleted_id', async () => {
    const fetchMock = mockFetchRoutes({ projects_delete: mcpResult({ deleted_id: 4 }) })
    const { deleteProject } = setup()

    const out = await deleteProject(4)

    expect(out).toEqual({ success: true, deleted_id: 4 })
    expect(mcpArgsAt(fetchMock)).toEqual({ project_id: 4 })
  })

  it('returns failure on error', async () => {
    mockFetchRoutes({ projects_delete: mcpResult({ error: 'in use' }) })
    const { deleteProject } = setup()
    expect(await deleteProject(4)).toEqual({ success: false, error: 'in use' })
  })
})

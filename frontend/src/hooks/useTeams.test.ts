import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useTeams } from './useTeams'
import { mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import { mcpResult, mcpArgsAt, mcpToolAt, mcpCallsTo } from './mcpEnvelope.testhelper'

const setup = () => renderHook(() => useTeams()).result.current

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

const team = (over: Record<string, any> = {}) => ({
  id: 1,
  name: 'Core',
  slug: 'core',
  ...over,
})

describe('useTeams.listTeams', () => {
  it('returns teams and defaults include flags to false', async () => {
    const teams = [team()]
    const fetchMock = mockFetchRoutes({ teams_list_all: mcpResult({ teams, count: 1 }) })
    const { listTeams } = setup()

    const out = await listTeams()

    expect(out).toEqual(teams)
    expect(mcpToolAt(fetchMock)).toBe('teams_list_all')
    expect(mcpArgsAt(fetchMock)).toMatchObject({ include_inactive: false, include_projects: false })
  })

  it('forwards tags and include flags', async () => {
    const fetchMock = mockFetchRoutes({ teams_list_all: mcpResult({ teams: [], count: 0 }) })
    const { listTeams } = setup()
    await listTeams({ tags: ['eng'], include_inactive: true, include_projects: true })
    expect(mcpArgsAt(fetchMock)).toMatchObject({ tags: ['eng'], include_inactive: true, include_projects: true })
  })

  it('returns [] when teams field missing', async () => {
    mockFetchRoutes({ teams_list_all: mcpResult({ count: 0 }) })
    const { listTeams } = setup()
    expect(await listTeams()).toEqual([])
  })

  it('throws when the result carries an error', async () => {
    mockFetchRoutes({ teams_list_all: mcpResult({ error: 'Not authenticated' }) })
    const { listTeams } = setup()
    await expect(listTeams()).rejects.toThrow('Not authenticated')
  })
})

describe('useTeams.getTeam', () => {
  it('returns the team and defaults include_members true, include_projects false', async () => {
    const fetchMock = mockFetchRoutes({ teams_fetch: mcpResult({ team: team() }) })
    const { getTeam } = setup()

    const out = await getTeam('core')

    expect(out).toEqual(team())
    expect(mcpArgsAt(fetchMock)).toEqual({ team: 'core', include_members: true, include_projects: false })
  })

  it('honors include flags', async () => {
    const fetchMock = mockFetchRoutes({ teams_fetch: mcpResult({ team: team() }) })
    const { getTeam } = setup()
    await getTeam(5, false, true)
    expect(mcpArgsAt(fetchMock)).toEqual({ team: 5, include_members: false, include_projects: true })
  })

  it('throws when the result carries an error', async () => {
    mockFetchRoutes({ teams_fetch: mcpResult({ error: 'forbidden' }) })
    const { getTeam } = setup()
    await expect(getTeam('core')).rejects.toThrow('forbidden')
  })

  it('returns null when team is absent', async () => {
    mockFetchRoutes({ teams_fetch: mcpResult({}) })
    const { getTeam } = setup()
    expect(await getTeam('core')).toBeNull()
  })
})

describe('useTeams.createTeam', () => {
  it('sends all creation fields and returns the envelope', async () => {
    const resp = { success: true, team: team() }
    const fetchMock = mockFetchRoutes({ teams_upsert: mcpResult(resp) })
    const { createTeam } = setup()

    const out = await createTeam({
      name: 'Core',
      slug: 'core',
      description: 'd',
      owner: 'alice',
      tags: ['t'],
    })

    expect(out).toEqual(resp)
    expect(mcpArgsAt(fetchMock)).toMatchObject({
      name: 'Core',
      slug: 'core',
      owner: 'alice',
      tags: ['t'],
    })
  })

  it.each([
    [null, 'Failed to create team'],
    [{ error: 'slug taken' }, 'slug taken'],
  ])('throws when the upsert fails (%o)', async (result, message) => {
    mockFetchRoutes({ teams_upsert: mcpResult(result) })
    const { createTeam } = setup()
    await expect(createTeam({ name: 'X' })).rejects.toThrow(message)
  })
})

describe('useTeams.updateTeam', () => {
  it('fetches the existing team then upserts with its slug and resolved name', async () => {
    const fetchMock = mockFetchRoutes({
      'auth/me': { json: { user_id: 1, scopes: ['*'] } },
      teams_fetch: mcpResult({ team: team({ name: 'Core', slug: 'core' }) }),
      teams_upsert: mcpResult({ success: true, team: team({ description: 'new' }) }),
    })
    const { updateTeam } = setup()

    const out = await updateTeam('core', { description: 'new', is_active: false })

    expect(out).toMatchObject({ success: true })
    // last MCP call is the upsert
    const upsertArgs = mcpArgsAt(fetchMock)
    expect(upsertArgs).toMatchObject({ name: 'Core', slug: 'core', description: 'new', is_active: false })
  })

  it('uses the supplied name when provided, keeping existing slug', async () => {
    const fetchMock = mockFetchRoutes({
      'auth/me': { json: { user_id: 1, scopes: ['*'] } },
      teams_fetch: mcpResult({ team: team({ name: 'Core', slug: 'core' }) }),
      teams_upsert: mcpResult({ success: true }),
    })
    const { updateTeam } = setup()

    await updateTeam('core', { name: 'Renamed' })

    expect(mcpArgsAt(fetchMock)).toMatchObject({ name: 'Renamed', slug: 'core' })
  })

  it('throws without upserting when the team is missing', async () => {
    const fetchMock = mockFetchRoutes({
      'auth/me': { json: { user_id: 1, scopes: ['*'] } },
      teams_fetch: mcpResult({ error: 'Team not found: ghost' }),
      teams_upsert: mcpResult({ success: true }),
    })
    const { updateTeam } = setup()

    await expect(updateTeam('ghost', { description: 'x' })).rejects.toThrow('Team not found: ghost')
    const upsertCalls = mcpCallsTo(fetchMock, 'teams_upsert')
    expect(upsertCalls).toHaveLength(0)
  })
})

describe('useTeams.addMember', () => {
  it('defaults role to member', async () => {
    const fetchMock = mockFetchRoutes({ teams_team_add_member: mcpResult({ success: true }) })
    const { addMember } = setup()

    await addMember('core', 'bob')

    expect(mcpArgsAt(fetchMock)).toEqual({ team: 'core', person: 'bob', role: 'member' })
  })

  it('passes an explicit role and throws the server error', async () => {
    const fetchMock = mockFetchRoutes({ teams_team_add_member: mcpResult({ error: 'Person not found: 2' }) })
    const { addMember } = setup()

    await expect(addMember(1, 2, 'admin')).rejects.toThrow('Person not found: 2')
    expect(mcpArgsAt(fetchMock)).toEqual({ team: 1, person: 2, role: 'admin' })
  })
})

describe('useTeams.removeMember', () => {
  it('sends team and person', async () => {
    const fetchMock = mockFetchRoutes({ teams_team_remove_member: mcpResult({ success: true }) })
    const { removeMember } = setup()

    await removeMember('core', 'bob')

    expect(mcpArgsAt(fetchMock)).toEqual({ team: 'core', person: 'bob' })
  })

  it('throws a generic error on empty result', async () => {
    mockFetchRoutes({ teams_team_remove_member: mcpResult(null) })
    const { removeMember } = setup()
    await expect(removeMember('core', 'bob')).rejects.toThrow('Failed to remove member')
  })
})

describe('useTeams.listMembers', () => {
  it('returns the team members via teams_fetch', async () => {
    const members = [{ id: 1, identifier: 'bob', display_name: 'Bob' }]
    const fetchMock = mockFetchRoutes({ teams_fetch: mcpResult({ team: team({ members }) }) })
    const { listMembers } = setup()

    const out = await listMembers('core')

    expect(out).toEqual(members)
    expect(mcpArgsAt(fetchMock)).toEqual({ team: 'core', include_members: true, include_projects: false })
  })

  it('returns [] when team has no members', async () => {
    mockFetchRoutes({ teams_fetch: mcpResult({ team: team() }) })
    const { listMembers } = setup()
    expect(await listMembers('core')).toEqual([])
  })
})

describe('useTeams.getPersonTeams', () => {
  it('returns a person teams via people_fetch with include_teams', async () => {
    const teams = [team()]
    const fetchMock = mockFetchRoutes({ people_fetch: mcpResult({ person: { teams } }) })
    const { getPersonTeams } = setup()

    const out = await getPersonTeams('alice')

    expect(out).toEqual(teams)
    expect(mcpArgsAt(fetchMock)).toEqual({ identifier: 'alice', include_teams: true })
  })

  it('returns [] when person has no teams', async () => {
    mockFetchRoutes({ people_fetch: mcpResult({ person: {} }) })
    const { getPersonTeams } = setup()
    expect(await getPersonTeams('alice')).toEqual([])
  })
})

describe('useTeams.listProjectTeams', () => {
  it('returns project teams via projects_fetch with include_teams', async () => {
    const teams = [team()]
    const fetchMock = mockFetchRoutes({ projects_fetch: mcpResult({ project: { teams } }) })
    const { listProjectTeams } = setup()

    const out = await listProjectTeams(3)

    expect(out).toEqual(teams)
    expect(mcpArgsAt(fetchMock)).toEqual({ project_id: 3, include_teams: true })
  })

  it('returns [] when project has no teams', async () => {
    mockFetchRoutes({ projects_fetch: mcpResult({ project: {} }) })
    const { listProjectTeams } = setup()
    expect(await listProjectTeams(3)).toEqual([])
  })
})

describe('useTeams.assignTeamToProject', () => {
  it('short-circuits to success when the team is already assigned', async () => {
    const fetchMock = mockFetchRoutes({
      'auth/me': { json: { user_id: 1, scopes: ['*'] } },
      projects_fetch: mcpResult({ project: { teams: [team({ id: 9 })] } }),
      projects_upsert: mcpResult({ success: true }),
    })
    const { assignTeamToProject } = setup()

    await assignTeamToProject(3, 9)

    const upsertCalls = mcpCallsTo(fetchMock, 'projects_upsert')
    expect(upsertCalls).toHaveLength(0)
  })

  it('appends the new team id and upserts', async () => {
    const fetchMock = mockFetchRoutes({
      'auth/me': { json: { user_id: 1, scopes: ['*'] } },
      projects_fetch: mcpResult({ project: { teams: [team({ id: 1 })] } }),
      projects_upsert: mcpResult({ success: true }),
    })
    const { assignTeamToProject } = setup()

    await assignTeamToProject(3, 2)

    expect(mcpArgsAt(fetchMock)).toEqual({ project_id: 3, team_ids: [1, 2] })
  })
})

describe('useTeams project team fetch failures', () => {
  it.each([
    ['assignTeamToProject' as const],
    ['unassignTeamFromProject' as const],
  ])('%s throws without rewriting the team list', async (fn) => {
    const fetchMock = mockFetchRoutes({
      'auth/me': { json: { user_id: 1, scopes: ['*'] } },
      projects_fetch: mcpResult({ error: 'Project not found: 3' }),
      projects_upsert: mcpResult({ success: true }),
    })
    const hook = setup()

    await expect(hook[fn](3, 2)).rejects.toThrow('Project not found: 3')
    expect(mcpCallsTo(fetchMock, 'projects_upsert')).toHaveLength(0)
  })
})

describe('useTeams.unassignTeamFromProject', () => {
  it('short-circuits to success when the team is not assigned', async () => {
    const fetchMock = mockFetchRoutes({
      'auth/me': { json: { user_id: 1, scopes: ['*'] } },
      projects_fetch: mcpResult({ project: { teams: [team({ id: 1 })] } }),
      projects_upsert: mcpResult({ success: true }),
    })
    const { unassignTeamFromProject } = setup()

    await unassignTeamFromProject(3, 99)

    const upsertCalls = mcpCallsTo(fetchMock, 'projects_upsert')
    expect(upsertCalls).toHaveLength(0)
  })

  it('refuses to remove the last team', async () => {
    const fetchMock = mockFetchRoutes({
      'auth/me': { json: { user_id: 1, scopes: ['*'] } },
      projects_fetch: mcpResult({ project: { teams: [team({ id: 7 })] } }),
      projects_upsert: mcpResult({ success: true }),
    })
    const { unassignTeamFromProject } = setup()

    await expect(unassignTeamFromProject(3, 7)).rejects.toThrow('Cannot remove the last team')
    const upsertCalls = mcpCallsTo(fetchMock, 'projects_upsert')
    expect(upsertCalls).toHaveLength(0)
  })

  it('removes the team and upserts the remaining ids', async () => {
    const fetchMock = mockFetchRoutes({
      'auth/me': { json: { user_id: 1, scopes: ['*'] } },
      projects_fetch: mcpResult({ project: { teams: [team({ id: 1 }), team({ id: 2 })] } }),
      projects_upsert: mcpResult({ success: true }),
    })
    const { unassignTeamFromProject } = setup()

    await unassignTeamFromProject(3, 1)

    expect(mcpArgsAt(fetchMock)).toEqual({ project_id: 3, team_ids: [2] })
  })
})

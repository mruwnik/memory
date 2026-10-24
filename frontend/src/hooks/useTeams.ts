import { useCallback } from 'react'
import { useMCP } from './useMCP'

export interface TeamProject {
  id: number
  title: string
  state: string
  repo_path: string | null
}

export interface TeamOwner {
  id: number
  identifier: string
  display_name: string | null
  contributor_status?: string
}

export interface Team {
  id: number
  name: string
  slug: string
  description: string | null
  owner_id: number | null
  owner: TeamOwner | null
  tags: string[]
  // Discord snowflakes exceed 2^53, so the API sends them as strings
  discord_role_id: string | null
  discord_guild_id: string | null
  auto_sync_discord: boolean
  github_team_id: number | null
  github_team_slug: string | null
  github_org: string | null
  auto_sync_github: boolean
  is_active: boolean
  created_at: string | null
  archived_at: string | null
  members?: TeamMember[]
  member_count?: number
  projects?: TeamProject[]
  project_count?: number
}

export interface TeamMember {
  id: number
  identifier: string
  display_name: string
  contributor_status?: string
  role?: string
}

export interface TeamCreate {
  name: string
  slug?: string
  description?: string
  owner?: string | number
  tags?: string[]
  // An empty guild or github_org unlinks that integration
  guild?: string
  discord_role?: string
  auto_sync_discord?: boolean
  github_org?: string
  github_team_slug?: string
  auto_sync_github?: boolean
}

export interface TeamUpdate {
  name?: string
  description?: string
  owner?: string | number | null
  tags?: string[]
  guild?: string
  discord_role?: string
  auto_sync_discord?: boolean
  github_org?: string
  github_team_slug?: string
  auto_sync_github?: boolean
  is_active?: boolean
}

/** A save that succeeded, with what the Discord/GitHub sync couldn't do. */
export interface TeamSaved {
  team?: Team
  warnings?: string[]
}

type Envelope = { error?: string }

// MCP tools report failures as `{error}` in a normal result, so turn them into throws.
const unwrap = <T extends Envelope>(result: T[] | undefined, fallback: string): T => {
  const r = result?.[0]
  if (!r || r.error) throw new Error(r?.error || fallback)
  return r
}

export interface TeamFilters {
  tags?: string[]
  include_inactive?: boolean
  include_projects?: boolean
}

export const useTeams = () => {
  const { mcpCall } = useMCP()

  const listTeams = useCallback(async (filters: TeamFilters = {}): Promise<Team[]> => {
    const result = await mcpCall<({ teams?: Team[] } & Envelope)[]>('teams_list_all', {
      tags: filters.tags,
      include_inactive: filters.include_inactive ?? false,
      include_projects: filters.include_projects ?? false,
    })
    return unwrap(result, 'Failed to load teams').teams || []
  }, [mcpCall])

  const getTeam = useCallback(async (team: string | number, includeMembers = true, includeProjects = false): Promise<Team | null> => {
    const result = await mcpCall<({ team?: Team } & Envelope)[]>('teams_fetch', {
      team,
      include_members: includeMembers,
      include_projects: includeProjects,
    })
    return unwrap(result, 'Failed to load team').team || null
  }, [mcpCall])

  const createTeam = useCallback(async (data: TeamCreate): Promise<TeamSaved> => {
    const result = await mcpCall<(TeamSaved & Envelope)[]>('teams_upsert', data)
    return unwrap(result, 'Failed to create team')
  }, [mcpCall])

  const updateTeam = useCallback(async (team: string | number, data: TeamUpdate): Promise<TeamSaved> => {
    // The upsert identifies the team by slug and requires a name
    const existing = await getTeam(team, false, false)
    if (!existing) throw new Error('Team not found')
    const result = await mcpCall<(TeamSaved & Envelope)[]>('teams_upsert', {
      ...data,
      name: data.name ?? existing.name,
      slug: existing.slug,
    })
    return unwrap(result, 'Failed to update team')
  }, [mcpCall, getTeam])

  /**
   * Add a person to a team with a specific role.
   * @param team - Team slug or ID
   * @param person - Person identifier or ID
   * @param role - Team role: "member", "lead", or "admin" (default: "member")
   *               Maps to project access: member->contributor, lead->manager, admin->admin
   */
  const addMember = useCallback(async (team: string | number, person: string | number, role: 'member' | 'lead' | 'admin' = 'member'): Promise<void> => {
    unwrap(await mcpCall<Envelope[]>('teams_team_add_member', { team, person, role }), 'Failed to add member')
  }, [mcpCall])

  const removeMember = useCallback(async (team: string | number, person: string | number): Promise<void> => {
    unwrap(await mcpCall<Envelope[]>('teams_team_remove_member', { team, person }), 'Failed to remove member')
  }, [mcpCall])

  const listMembers = useCallback(async (team: string | number): Promise<TeamMember[]> => {
    const result = await mcpCall<({ team?: Team } & Envelope)[]>('teams_fetch', {
      team,
      include_members: true,
      include_projects: false,
    })
    return unwrap(result, 'Failed to load team members').team?.members || []
  }, [mcpCall])

  const getPersonTeams = useCallback(async (person: string | number): Promise<Team[]> => {
    // Use people_fetch with include_teams to get a person's teams
    const result = await mcpCall<{ person: { teams?: Team[] }; error?: string }[]>('people_fetch', {
      identifier: person,
      include_teams: true,
    })
    return result?.[0]?.person?.teams || []
  }, [mcpCall])

  // Project-Team associations
  const listProjectTeams = useCallback(async (project: number): Promise<Team[]> => {
    // Assign/unassign rewrite the whole team list from this, so a failed fetch must not read as "no teams"
    const result = await mcpCall<({ project?: { teams?: Team[] } } & Envelope)[]>('projects_fetch', {
      project_id: project,
      include_teams: true,
    })
    return unwrap(result, 'Failed to load project teams').project?.teams || []
  }, [mcpCall])

  const assignTeamToProject = useCallback(async (project: number, teamId: number): Promise<void> => {
    const currentTeamIds = (await listProjectTeams(project)).map(t => t.id)
    if (currentTeamIds.includes(teamId)) return
    unwrap(await mcpCall<Envelope[]>('projects_upsert', {
      project_id: project,
      team_ids: [...currentTeamIds, teamId],
    }), 'Failed to assign team')
  }, [mcpCall, listProjectTeams])

  const unassignTeamFromProject = useCallback(async (project: number, teamId: number): Promise<void> => {
    const currentTeamIds = (await listProjectTeams(project)).map(t => t.id)
    if (!currentTeamIds.includes(teamId)) return
    const newTeamIds = currentTeamIds.filter(id => id !== teamId)
    if (newTeamIds.length === 0) throw new Error('Cannot remove the last team - projects require at least one team')
    unwrap(await mcpCall<Envelope[]>('projects_upsert', {
      project_id: project,
      team_ids: newTeamIds,
    }), 'Failed to unassign team')
  }, [mcpCall, listProjectTeams])

  return {
    listTeams,
    getTeam,
    createTeam,
    updateTeam,
    addMember,
    removeMember,
    listMembers,
    getPersonTeams,
    listProjectTeams,
    assignTeamToProject,
    unassignTeamFromProject,
  }
}

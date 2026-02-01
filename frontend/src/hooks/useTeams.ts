import { useCallback } from 'react'
import { useMCP } from './useMCP'

export interface TeamProject {
  id: number
  title: string
  state: string
  repo_path: string | null
}

export interface Team {
  id: number
  name: string
  slug: string
  description: string | null
  tags: string[]
  discord_role_id: number | null
  discord_guild_id: number | null
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
}

export interface TeamCreate {
  name: string
  slug: string
  description?: string
  tags?: string[]
  discord_role_id?: number
  discord_guild_id?: number
  auto_sync_discord?: boolean
  github_team_id?: number
  github_team_slug?: string
  github_org?: string
  auto_sync_github?: boolean
}

export interface TeamUpdate {
  name?: string
  description?: string
  tags?: string[]
  discord_role_id?: number
  discord_guild_id?: number
  auto_sync_discord?: boolean
  github_team_id?: number
  github_team_slug?: string
  github_org?: string
  auto_sync_github?: boolean
  is_active?: boolean
}

export interface TeamFilters {
  tags?: string[]
  include_inactive?: boolean
  include_projects?: boolean
}

export const useTeams = () => {
  const { mcpCall } = useMCP()

  const listTeams = useCallback(async (filters: TeamFilters = {}): Promise<Team[]> => {
    const result = await mcpCall<{ teams: Team[]; count: number }[]>('teams_team_list', {
      tags: filters.tags,
      include_inactive: filters.include_inactive ?? false,
      include_projects: filters.include_projects ?? false,
    })
    return result?.[0]?.teams || []
  }, [mcpCall])

  const getTeam = useCallback(async (team: string | number, includeMembers = true, includeProjects = false): Promise<Team | null> => {
    const result = await mcpCall<{ team: Team; error?: string }[]>('teams_team_get', {
      team,
      include_members: includeMembers,
      include_projects: includeProjects,
    })
    if (result?.[0]?.error) return null
    return result?.[0]?.team || null
  }, [mcpCall])

  const createTeam = useCallback(async (data: TeamCreate): Promise<{ success: boolean; team?: Team; error?: string }> => {
    const result = await mcpCall<{ success: boolean; team?: Team; error?: string }[]>('teams_team_create', {
      name: data.name,
      slug: data.slug,
      description: data.description,
      tags: data.tags,
      discord_role_id: data.discord_role_id,
      discord_guild_id: data.discord_guild_id,
      auto_sync_discord: data.auto_sync_discord,
      github_team_id: data.github_team_id,
      github_team_slug: data.github_team_slug,
      github_org: data.github_org,
      auto_sync_github: data.auto_sync_github,
    })
    return result?.[0] || { success: false, error: 'Unknown error' }
  }, [mcpCall])

  const updateTeam = useCallback(async (team: string | number, data: TeamUpdate): Promise<{ success: boolean; team?: Team; error?: string }> => {
    const result = await mcpCall<{ success: boolean; team?: Team; error?: string }[]>('teams_team_update', {
      team,
      name: data.name,
      description: data.description,
      tags: data.tags,
      discord_role_id: data.discord_role_id,
      discord_guild_id: data.discord_guild_id,
      auto_sync_discord: data.auto_sync_discord,
      github_team_id: data.github_team_id,
      github_team_slug: data.github_team_slug,
      github_org: data.github_org,
      auto_sync_github: data.auto_sync_github,
      is_active: data.is_active,
    })
    return result?.[0] || { success: false, error: 'Unknown error' }
  }, [mcpCall])

  /**
   * Add a person to a team with a specific role.
   * @param team - Team slug or ID
   * @param person - Person identifier or ID
   * @param role - Team role: "member", "lead", or "admin" (default: "member")
   *               Maps to project access: member->contributor, lead->manager, admin->admin
   */
  const addMember = useCallback(async (team: string | number, person: string | number, role: 'member' | 'lead' | 'admin' = 'member'): Promise<{ success: boolean; error?: string }> => {
    const result = await mcpCall<{ success: boolean; error?: string }[]>('teams_team_add_member', {
      team,
      person,
      role,
    })
    return result?.[0] || { success: false, error: 'Unknown error' }
  }, [mcpCall])

  const removeMember = useCallback(async (team: string | number, person: string | number): Promise<{ success: boolean; error?: string }> => {
    const result = await mcpCall<{ success: boolean; error?: string }[]>('teams_team_remove_member', {
      team,
      person,
    })
    return result?.[0] || { success: false, error: 'Unknown error' }
  }, [mcpCall])

  const listMembers = useCallback(async (team: string | number): Promise<TeamMember[]> => {
    const result = await mcpCall<{ members: TeamMember[]; count: number; error?: string }[]>('teams_team_list_members', {
      team,
    })
    return result?.[0]?.members || []
  }, [mcpCall])

  const getPersonTeams = useCallback(async (person: string | number): Promise<Team[]> => {
    const result = await mcpCall<{ teams: Team[]; count: number; error?: string }[]>('teams_person_teams', {
      person,
    })
    return result?.[0]?.teams || []
  }, [mcpCall])

  // Project-Team associations
  const listProjectTeams = useCallback(async (project: number | string): Promise<Team[]> => {
    const result = await mcpCall<{ teams: Team[]; count: number; error?: string }[]>('teams_project_list_teams', {
      project,
    })
    return result?.[0]?.teams || []
  }, [mcpCall])

  const assignTeamToProject = useCallback(async (project: number | string, team: string | number): Promise<{ success: boolean; error?: string }> => {
    const result = await mcpCall<{ success: boolean; error?: string }[]>('teams_project_assign_team', {
      project,
      team,
    })
    return result?.[0] || { success: false, error: 'Unknown error' }
  }, [mcpCall])

  const unassignTeamFromProject = useCallback(async (project: number | string, team: string | number): Promise<{ success: boolean; error?: string }> => {
    const result = await mcpCall<{ success: boolean; error?: string }[]>('teams_project_unassign_team', {
      project,
      team,
    })
    return result?.[0] || { success: false, error: 'Unknown error' }
  }, [mcpCall])

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

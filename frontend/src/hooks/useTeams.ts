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
  role?: string
}

export interface TeamCreate {
  name: string
  slug?: string
  description?: string
  tags?: string[]
  guild?: number | string
  discord_role?: number | string
  auto_sync_discord?: boolean
  github_org?: string
  github_team_slug?: string
  auto_sync_github?: boolean
}

export interface TeamUpdate {
  name?: string
  description?: string
  tags?: string[]
  guild?: number | string
  discord_role?: number | string
  auto_sync_discord?: boolean
  github_org?: string
  github_team_slug?: string
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
    const result = await mcpCall<{ teams: Team[]; count: number }[]>('teams_list_all', {
      tags: filters.tags,
      include_inactive: filters.include_inactive ?? false,
      include_projects: filters.include_projects ?? false,
    })
    return result?.[0]?.teams || []
  }, [mcpCall])

  const getTeam = useCallback(async (team: string | number, includeMembers = true, includeProjects = false): Promise<Team | null> => {
    const result = await mcpCall<{ team: Team; error?: string }[]>('teams_fetch', {
      team,
      include_members: includeMembers,
      include_projects: includeProjects,
    })
    if (result?.[0]?.error) return null
    return result?.[0]?.team || null
  }, [mcpCall])

  const createTeam = useCallback(async (data: TeamCreate): Promise<{ success: boolean; team?: Team; error?: string }> => {
    const result = await mcpCall<{ success: boolean; team?: Team; error?: string }[]>('teams_upsert', {
      name: data.name,
      slug: data.slug,
      description: data.description,
      tags: data.tags,
      guild: data.guild,
      discord_role: data.discord_role,
      auto_sync_discord: data.auto_sync_discord,
      github_org: data.github_org,
      github_team_slug: data.github_team_slug,
      auto_sync_github: data.auto_sync_github,
    })
    return result?.[0] || { success: false, error: 'Unknown error' }
  }, [mcpCall])

  const updateTeam = useCallback(async (team: string | number, data: TeamUpdate): Promise<{ success: boolean; team?: Team; error?: string }> => {
    // Get existing team to get the name (required for upsert)
    const existing = await getTeam(team, false, false)
    if (!existing) {
      return { success: false, error: 'Team not found' }
    }

    const result = await mcpCall<{ success: boolean; team?: Team; error?: string }[]>('teams_upsert', {
      name: data.name ?? existing.name,
      slug: existing.slug,  // Use existing slug to identify the team
      description: data.description,
      tags: data.tags,
      guild: data.guild,
      discord_role: data.discord_role,
      auto_sync_discord: data.auto_sync_discord,
      github_org: data.github_org,
      github_team_slug: data.github_team_slug,
      auto_sync_github: data.auto_sync_github,
      is_active: data.is_active,
    })
    return result?.[0] || { success: false, error: 'Unknown error' }
  }, [mcpCall, getTeam])

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
    // Use teams_fetch with include_members to get members
    const result = await mcpCall<{ team: Team; error?: string }[]>('teams_fetch', {
      team,
      include_members: true,
      include_projects: false,
    })
    return result?.[0]?.team?.members || []
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
    const result = await mcpCall<{ project: { teams?: Team[] }; error?: string }[]>('projects_fetch', {
      project_id: project,
      include_teams: true,
    })
    return result?.[0]?.project?.teams || []
  }, [mcpCall])

  const assignTeamToProject = useCallback(async (project: number, teamId: number): Promise<{ success: boolean; error?: string }> => {
    // Fetch current teams, add the new one, update via projects_upsert
    const currentTeams = await listProjectTeams(project)
    const currentTeamIds = currentTeams.map(t => t.id)
    if (currentTeamIds.includes(teamId)) {
      return { success: true } // Already assigned
    }
    const newTeamIds = [...currentTeamIds, teamId]
    const result = await mcpCall<{ success: boolean; error?: string }[]>('projects_upsert', {
      project_id: project,
      team_ids: newTeamIds,
    })
    return result?.[0] || { success: false, error: 'Unknown error' }
  }, [mcpCall, listProjectTeams])

  const unassignTeamFromProject = useCallback(async (project: number, teamId: number): Promise<{ success: boolean; error?: string }> => {
    // Fetch current teams, remove the team, update via projects_upsert
    const currentTeams = await listProjectTeams(project)
    const currentTeamIds = currentTeams.map(t => t.id)
    if (!currentTeamIds.includes(teamId)) {
      return { success: true } // Already not assigned
    }
    const newTeamIds = currentTeamIds.filter(id => id !== teamId)
    if (newTeamIds.length === 0) {
      return { success: false, error: 'Cannot remove the last team - projects require at least one team' }
    }
    const result = await mcpCall<{ success: boolean; error?: string }[]>('projects_upsert', {
      project_id: project,
      team_ids: newTeamIds,
    })
    return result?.[0] || { success: false, error: 'Unknown error' }
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

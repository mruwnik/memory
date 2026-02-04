import { useCallback } from 'react'
import { useMCP } from './useMCP'

// Types for Projects
export interface ProjectTeam {
  id: number
  name: string
  slug: string
  member_count: number | null
}

export interface ProjectOwner {
  id: number
  identifier: string
  display_name: string | null
}

export interface JournalEntry {
  id: number
  target_type: 'source_item' | 'project' | 'team' | 'poll'
  target_id: number
  creator_id: number | null
  project_id: number | null
  content: string
  private: boolean
  created_at: string
}

export interface Project {
  id: number
  title: string
  description: string | null
  state: 'open' | 'closed'
  // GitHub info (null for standalone projects)
  repo_path: string | null
  github_id: number | null
  number: number | null
  // Hierarchy
  parent_id: number | null
  children_count: number
  // Owner and due date
  owner_id: number | null
  due_on: string | null  // ISO datetime string
  owner?: ProjectOwner | null
  // Teams (optional)
  teams?: ProjectTeam[]
  // Journal entries (optional)
  journal_entries?: JournalEntry[]
}

export interface ProjectTreeNode {
  id: number
  title: string
  description: string | null
  state: string
  repo_path: string | null
  parent_id: number | null
  children: ProjectTreeNode[]
}

export interface ProjectCreate {
  title: string
  team_ids: number[]  // Required: list of team IDs to assign this project to
  description?: string | null
  state?: 'open' | 'closed'
  parent_id?: number | null
  owner_id?: number | null
  due_on?: string | null  // ISO datetime string
}

export interface ProjectUpdate {
  title?: string
  description?: string | null
  state?: 'open' | 'closed'
  parent_id?: number | null
  clear_parent?: boolean  // If true, removes the parent
  owner_id?: number | null
  clear_owner?: boolean  // If true, removes the owner
  due_on?: string | null  // ISO datetime string
  clear_due_on?: boolean  // If true, removes the due date
}

export interface ProjectFilters {
  state?: 'open' | 'closed'
  parent_id?: number  // Use 0 for root-level only
  include_teams?: boolean
  limit?: number
  offset?: number
  search?: string
}

export const useProjects = () => {
  const { mcpCall } = useMCP()

  const listProjects = useCallback(async (options: ProjectFilters = {}): Promise<Project[]> => {
    const result = await mcpCall<{ projects: Project[]; count: number; error?: string }[]>('projects_list_all', {
      state: options.state,
      parent_id: options.parent_id,
      include_teams: options.include_teams ?? false,
      limit: options.limit,
      offset: options.offset,
      search: options.search,
    })
    if (result?.[0]?.error) {
      throw new Error(result[0].error)
    }
    return result?.[0]?.projects || []
  }, [mcpCall])

  const getProjectTree = useCallback(async (options?: {
    state?: 'open' | 'closed'
  }): Promise<ProjectTreeNode[]> => {
    const result = await mcpCall<{ tree: ProjectTreeNode[]; count: number; error?: string }[]>('projects_list_all', {
      state: options?.state,
      as_tree: true,
    })
    if (result?.[0]?.error) {
      throw new Error(result[0].error)
    }
    return result?.[0]?.tree || []
  }, [mcpCall])

  const getProject = useCallback(async (
    id: number,
    options: { includeTeams?: boolean; includeJournal?: boolean } = {}
  ): Promise<{ project: Project | null; journal_entries?: JournalEntry[] }> => {
    const result = await mcpCall<{ project: Project | null; journal_entries?: JournalEntry[]; error?: string }[]>('projects_fetch', {
      project_id: id,
      include_teams: options.includeTeams ?? false,
      include_journal: options.includeJournal ?? false,
    })
    if (result?.[0]?.error) {
      return { project: null }
    }
    return {
      project: result?.[0]?.project || null,
      journal_entries: result?.[0]?.journal_entries,
    }
  }, [mcpCall])

  const createProject = useCallback(async (data: ProjectCreate): Promise<{ success: boolean; project?: Project; error?: string }> => {
    const result = await mcpCall<{ success?: boolean; project?: Project; error?: string }[]>('projects_upsert', {
      title: data.title,
      team_ids: data.team_ids,
      description: data.description,
      state: data.state ?? 'open',
      parent_id: data.parent_id,
      owner_id: data.owner_id,
      due_on: data.due_on,
    })
    const response = result?.[0]
    if (response?.error) {
      return { success: false, error: response.error }
    }
    return { success: true, project: response?.project }
  }, [mcpCall])

  const updateProject = useCallback(async (id: number, data: ProjectUpdate): Promise<{ success: boolean; project?: Project; error?: string }> => {
    const result = await mcpCall<{ success?: boolean; project?: Project; error?: string }[]>('projects_upsert', {
      project_id: id,
      title: data.title,
      description: data.description,
      state: data.state,
      parent_id: data.parent_id,
      clear_parent: data.clear_parent,
      owner_id: data.owner_id,
      clear_owner: data.clear_owner,
      due_on: data.due_on,
      clear_due_on: data.clear_due_on,
    })
    const response = result?.[0]
    if (response?.error) {
      return { success: false, error: response.error }
    }
    return { success: true, project: response?.project }
  }, [mcpCall])

  const deleteProject = useCallback(async (id: number): Promise<{ success: boolean; deleted_id?: number; error?: string }> => {
    const result = await mcpCall<{ success?: boolean; deleted_id?: number; error?: string }[]>('projects_delete', {
      project_id: id,
    })
    const response = result?.[0]
    if (response?.error) {
      return { success: false, error: response.error }
    }
    return { success: true, deleted_id: response?.deleted_id }
  }, [mcpCall])

  return {
    listProjects,
    getProjectTree,
    getProject,
    createProject,
    updateProject,
    deleteProject,
  }
}

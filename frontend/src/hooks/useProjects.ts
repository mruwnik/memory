import { useCallback } from 'react'
import { useAuth } from './useAuth'

// Types for Projects
export interface ProjectTeam {
  id: number
  name: string
  slug: string
  member_count: number | null
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
  // Teams (optional)
  teams?: ProjectTeam[]
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
  description?: string | null
  state?: 'open' | 'closed'
  parent_id?: number | null
}

export interface ProjectUpdate {
  title?: string
  description?: string | null
  state?: 'open' | 'closed'
  parent_id?: number | null
}

export const useProjects = () => {
  const { apiCall } = useAuth()

  const listProjects = useCallback(async (options?: {
    state?: string
    parent_id?: number
    include_children?: boolean
    include_teams?: boolean
  }): Promise<Project[]> => {
    const params = new URLSearchParams()
    if (options?.state) params.append('state', options.state)
    if (options?.parent_id !== undefined) params.append('parent_id', String(options.parent_id))
    if (options?.include_children) params.append('include_children', 'true')
    if (options?.include_teams) params.append('include_teams', 'true')

    const url = `/projects${params.toString() ? `?${params.toString()}` : ''}`
    const response = await apiCall(url)
    if (!response.ok) throw new Error('Failed to fetch projects')
    return response.json()
  }, [apiCall])

  const getProjectTree = useCallback(async (options?: {
    state?: string
  }): Promise<ProjectTreeNode[]> => {
    const params = new URLSearchParams()
    if (options?.state) params.append('state', options.state)

    const url = `/projects/tree${params.toString() ? `?${params.toString()}` : ''}`
    const response = await apiCall(url)
    if (!response.ok) throw new Error('Failed to fetch project tree')
    return response.json()
  }, [apiCall])

  const getProject = useCallback(async (id: number, includeTeams = false): Promise<Project> => {
    const params = includeTeams ? '?include_teams=true' : ''
    const response = await apiCall(`/projects/${id}${params}`)
    if (!response.ok) throw new Error('Failed to fetch project')
    return response.json()
  }, [apiCall])

  const createProject = useCallback(async (data: ProjectCreate): Promise<Project> => {
    const response = await apiCall('/projects', {
      method: 'POST',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to create project')
    }
    return response.json()
  }, [apiCall])

  const updateProject = useCallback(async (id: number, data: ProjectUpdate): Promise<Project> => {
    const response = await apiCall(`/projects/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to update project')
    }
    return response.json()
  }, [apiCall])

  const deleteProject = useCallback(async (id: number): Promise<{ status: string; id: number }> => {
    const response = await apiCall(`/projects/${id}`, {
      method: 'DELETE',
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to delete project')
    }
    return response.json()
  }, [apiCall])

  return {
    listProjects,
    getProjectTree,
    getProject,
    createProject,
    updateProject,
    deleteProject,
  }
}

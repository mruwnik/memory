import { useCallback } from 'react'
import { useAuth } from './useAuth'

// Types for Projects
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
  }): Promise<Project[]> => {
    const params = new URLSearchParams()
    if (options?.state) params.append('state', options.state)
    if (options?.parent_id !== undefined) params.append('parent_id', String(options.parent_id))
    if (options?.include_children) params.append('include_children', 'true')

    const url = `/projects${params.toString() ? `?${params.toString()}` : ''}`
    return apiCall(url)
  }, [apiCall])

  const getProjectTree = useCallback(async (options?: {
    state?: string
  }): Promise<ProjectTreeNode[]> => {
    const params = new URLSearchParams()
    if (options?.state) params.append('state', options.state)

    const url = `/projects/tree${params.toString() ? `?${params.toString()}` : ''}`
    return apiCall(url)
  }, [apiCall])

  const getProject = useCallback(async (id: number): Promise<Project> => {
    return apiCall(`/projects/${id}`)
  }, [apiCall])

  const createProject = useCallback(async (data: ProjectCreate): Promise<Project> => {
    return apiCall('/projects', {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }, [apiCall])

  const updateProject = useCallback(async (id: number, data: ProjectUpdate): Promise<Project> => {
    return apiCall(`/projects/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
  }, [apiCall])

  const deleteProject = useCallback(async (id: number): Promise<{ status: string; id: number }> => {
    return apiCall(`/projects/${id}`, {
      method: 'DELETE',
    })
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

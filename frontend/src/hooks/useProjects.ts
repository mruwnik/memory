import { useState, useCallback } from 'react'
import { useAuth } from './useAuth'

export interface Milestone {
  id: number
  repo_path: string
  repo_name: string
  number: number
  title: string
  description: string | null
  state: string
  due_on: string | null
  open_issues: number
  closed_issues: number
  total_issues: number
  progress_percent: number
  github_created_at: string | null
  github_updated_at: string | null
  url: string
}

export interface RepoMilestones {
  repo_path: string
  repo_name: string
  owner: string
  milestones: Milestone[]
  total_open_milestones: number
  total_closed_milestones: number
}

export interface ProjectsOverview {
  repos: RepoMilestones[]
  total_repos: number
  total_open_milestones: number
  total_closed_milestones: number
  last_updated: string | null
}

export interface TrackedRepo {
  id: number
  repo_path: string
  owner: string
  name: string
  last_sync_at: string | null
}

export interface ProjectsFilters {
  state?: 'open' | 'closed' | null
  repoFilter?: string[]
  includeClosed?: boolean
}

export const useProjects = () => {
  const { apiCall } = useAuth()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const getMilestones = useCallback(
    async (filters: ProjectsFilters = {}): Promise<ProjectsOverview | null> => {
      setLoading(true)
      setError(null)

      try {
        const params = new URLSearchParams()
        if (filters.state) {
          params.append('state', filters.state)
        }
        if (filters.includeClosed) {
          params.append('include_closed', 'true')
        }
        if (filters.repoFilter && filters.repoFilter.length > 0) {
          filters.repoFilter.forEach((repo) => params.append('repo_filter', repo))
        }

        const queryString = params.toString()
        const url = `/projects/milestones${queryString ? `?${queryString}` : ''}`

        const response = await apiCall(url)
        if (!response.ok) {
          throw new Error(`Failed to fetch milestones: ${response.statusText}`)
        }

        const data = await response.json()
        return data
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to fetch milestones'
        setError(message)
        return null
      } finally {
        setLoading(false)
      }
    },
    [apiCall]
  )

  const getMilestone = useCallback(
    async (milestoneId: number): Promise<Milestone | null> => {
      setLoading(true)
      setError(null)

      try {
        const response = await apiCall(`/projects/milestones/${milestoneId}`)
        if (!response.ok) {
          throw new Error(`Failed to fetch milestone: ${response.statusText}`)
        }

        const data = await response.json()
        return data
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to fetch milestone'
        setError(message)
        return null
      } finally {
        setLoading(false)
      }
    },
    [apiCall]
  )

  const getTrackedRepos = useCallback(async (): Promise<TrackedRepo[]> => {
    setLoading(true)
    setError(null)

    try {
      const response = await apiCall('/projects/repos')
      if (!response.ok) {
        throw new Error(`Failed to fetch repos: ${response.statusText}`)
      }

      const data = await response.json()
      return data
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch repos'
      setError(message)
      return []
    } finally {
      setLoading(false)
    }
  }, [apiCall])

  return {
    getMilestones,
    getMilestone,
    getTrackedRepos,
    loading,
    error,
  }
}

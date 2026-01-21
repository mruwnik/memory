import { useCallback } from 'react'
import { useAuth } from './useAuth'

export type JobStatus = 'pending' | 'processing' | 'complete' | 'failed'

export interface Job {
  id: number
  job_type: string
  external_id: string | null
  status: JobStatus
  error_message: string | null
  result_id: number | null
  result_type: string | null
  params: Record<string, unknown>
  created_at: string
  updated_at: string
  completed_at: string | null
  attempts: number
}

export interface JobFilters {
  status?: JobStatus
  job_type?: string
  limit?: number
  offset?: number
  userId?: number  // Admin only: filter by specific user, omit for all users
}

export interface JobUser {
  id: number
  name: string
  email: string
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const contentType = response.headers.get('content-type')
  if (!contentType?.includes('application/json')) {
    const text = await response.text()
    throw new Error(`Expected JSON response but got ${contentType}: ${text.substring(0, 100)}`)
  }
  return await response.json()
}

export const useJobs = () => {
  const { apiCall } = useAuth()

  const listJobs = useCallback(async (filters: JobFilters = {}): Promise<Job[]> => {
    const params = new URLSearchParams()
    if (filters.status) params.set('status', filters.status)
    if (filters.job_type) params.set('job_type', filters.job_type)
    if (filters.limit) params.set('limit', filters.limit.toString())
    if (filters.offset) params.set('offset', filters.offset.toString())
    if (filters.userId !== undefined) params.set('user_id', filters.userId.toString())

    const query = params.toString()
    const response = await apiCall(`/jobs${query ? `?${query}` : ''}`)
    if (!response.ok) {
      throw new Error(`Failed to fetch jobs: ${response.status}`)
    }
    return parseJsonResponse<Job[]>(response)
  }, [apiCall])

  const getJob = useCallback(async (id: number): Promise<Job> => {
    const response = await apiCall(`/jobs/${id}`)
    if (!response.ok) {
      throw new Error(`Failed to fetch job: ${response.status}`)
    }
    return parseJsonResponse<Job>(response)
  }, [apiCall])

  const retryJob = useCallback(async (id: number): Promise<Job> => {
    const response = await apiCall(`/jobs/${id}/retry`, { method: 'POST' })
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }))
      throw new Error(error.detail || `Failed to retry job: ${response.status}`)
    }
    return parseJsonResponse<Job>(response)
  }, [apiCall])

  const reingestJob = useCallback(async (id: number): Promise<Job> => {
    const response = await apiCall(`/jobs/${id}/reingest`, { method: 'POST' })
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }))
      throw new Error(error.detail || `Failed to reingest job: ${response.status}`)
    }
    return parseJsonResponse<Job>(response)
  }, [apiCall])

  const getUsersWithJobs = useCallback(async (): Promise<JobUser[]> => {
    const response = await apiCall('/jobs/users/with-jobs')
    if (!response.ok) {
      throw new Error(`Failed to fetch job users: ${response.status}`)
    }
    return parseJsonResponse<JobUser[]>(response)
  }, [apiCall])

  return {
    listJobs,
    getJob,
    retryJob,
    reingestJob,
    getUsersWithJobs,
  }
}

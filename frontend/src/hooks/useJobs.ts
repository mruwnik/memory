import { useCallback } from 'react'
import { useAuth } from './useAuth'
import { apiError } from './apiError'

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
  user_id: number | null
}

export type JobSource = 'manual' | 'automatic'

export interface JobFilters {
  status?: JobStatus
  job_type?: string
  source?: JobSource
  limit?: number
  offset?: number
  userId?: number  // Admin only: filter by specific user, omit for all users
  created_after?: string  // ISO datetime
  created_before?: string  // ISO datetime
}

export type JobCounts = Record<JobStatus, number>

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

// The filters shared by the list and the counts endpoints
function jobFilterParams(filters: JobFilters): URLSearchParams {
  const params = new URLSearchParams()
  if (filters.job_type) params.set('job_type', filters.job_type)
  if (filters.source) params.set('source', filters.source)
  if (filters.created_after) params.set('created_after', filters.created_after)
  if (filters.created_before) params.set('created_before', filters.created_before)
  if (filters.userId !== undefined) params.set('user_id', filters.userId.toString())
  return params
}

export const useJobs = () => {
  const { apiCall } = useAuth()

  const listJobs = useCallback(async (filters: JobFilters = {}): Promise<Job[]> => {
    const params = jobFilterParams(filters)
    if (filters.status) params.set('status', filters.status)
    if (filters.limit) params.set('limit', filters.limit.toString())
    if (filters.offset) params.set('offset', filters.offset.toString())

    const query = params.toString()
    const response = await apiCall(`/jobs${query ? `?${query}` : ''}`)
    if (!response.ok) throw await apiError(response, 'Failed to fetch jobs')
    return parseJsonResponse<Job[]>(response)
  }, [apiCall])

  const getJobCounts = useCallback(async (filters: JobFilters = {}): Promise<JobCounts> => {
    const query = jobFilterParams(filters).toString()
    const response = await apiCall(`/jobs/counts${query ? `?${query}` : ''}`)
    if (!response.ok) throw await apiError(response, 'Failed to fetch job counts')
    return parseJsonResponse<JobCounts>(response)
  }, [apiCall])

  const getJob = useCallback(async (id: number): Promise<Job> => {
    const response = await apiCall(`/jobs/${id}`)
    if (!response.ok) throw await apiError(response, 'Failed to fetch job')
    return parseJsonResponse<Job>(response)
  }, [apiCall])

  const retryJob = useCallback(async (id: number): Promise<Job> => {
    const response = await apiCall(`/jobs/${id}/retry`, { method: 'POST' })
    if (!response.ok) throw await apiError(response, 'Failed to retry job')
    return parseJsonResponse<Job>(response)
  }, [apiCall])

  const reingestJob = useCallback(async (id: number): Promise<Job> => {
    const response = await apiCall(`/jobs/${id}/reingest`, { method: 'POST' })
    if (!response.ok) throw await apiError(response, 'Failed to reingest job')
    return parseJsonResponse<Job>(response)
  }, [apiCall])

  const getJobTypes = useCallback(async (): Promise<string[]> => {
    const response = await apiCall('/jobs/types')
    if (!response.ok) return []
    return parseJsonResponse<string[]>(response)
  }, [apiCall])

  const getUsersWithJobs = useCallback(async (): Promise<JobUser[]> => {
    const response = await apiCall('/jobs/users/with-jobs')
    if (!response.ok) throw await apiError(response, 'Failed to fetch job users')
    return parseJsonResponse<JobUser[]>(response)
  }, [apiCall])

  return {
    listJobs,
    getJobCounts,
    getJob,
    retryJob,
    reingestJob,
    getJobTypes,
    getUsersWithJobs,
  }
}

import { useCallback } from 'react'
import { useAuth, SERVER_URL } from './useAuth'
import { useMCP } from './useMCP'

export type PollStatus = 'open' | 'closed' | 'finalized' | 'cancelled'
export type AvailabilityLevel = 1 | 2 // 1 = available, 2 = if needed

export interface AvailabilitySlot {
  slot_start: string
  slot_end: string
  availability_level: AvailabilityLevel
}

export interface PollResponse {
  id: number
  respondent_name: string | null
  respondent_email: string | null
  person_id: number | null
  availabilities: AvailabilitySlot[]
  created_at: string
  updated_at: string
}

export interface Poll {
  id: number
  slug: string
  title: string
  description: string | null
  status: PollStatus
  datetime_start: string  // UTC ISO datetime
  datetime_end: string    // UTC ISO datetime
  slot_duration_minutes: number
  response_count: number
  created_at: string
  closes_at: string | null
  finalized_at: string | null
  finalized_time: string | null
  share_url?: string
  results_url?: string
}

export interface PollDetail extends Poll {
  responses: PollResponse[]
}

export interface SlotAggregation {
  slot_start: string
  slot_end: string
  available_count: number
  if_needed_count: number
  total_count: number
  respondents: string[]
}

export interface PollResults {
  poll: Poll
  response_count: number
  aggregated: SlotAggregation[]
  best_slots: SlotAggregation[]
}

export interface CreatePollRequest {
  title: string
  description?: string
  datetime_start: string  // UTC ISO datetime
  datetime_end: string    // UTC ISO datetime
  slot_duration?: 15 | 30 | 60
  closes_at?: string
}

export interface UpdatePollRequest {
  poll_id: number
  title?: string
  description?: string
  datetime_start?: string
  datetime_end?: string
  slot_duration?: 15 | 30 | 60
  closes_at?: string
  status?: 'open' | 'closed' | 'finalized'
  finalized_time?: string
}

export interface SubmitResponseRequest {
  respondent_name?: string
  respondent_email?: string
  availabilities: AvailabilitySlot[]
}

export interface SubmitResponseResult {
  response_id: number
  edit_token: string
  status: string
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const contentType = response.headers.get('content-type')
  if (!contentType?.includes('application/json')) {
    const text = await response.text()
    throw new Error(`Expected JSON response but got ${contentType}: ${text.substring(0, 100)}`)
  }
  return await response.json()
}

// Public API calls (no auth required)
async function publicFetch<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${SERVER_URL}${endpoint}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(error.detail || `Request failed: ${response.status}`)
  }

  return parseJsonResponse<T>(response)
}

export const usePolls = () => {
  const { mcpCall: rawMcpCall } = useMCP()

  // Wrapper that returns first result item (MCP returns array)
  const mcpCall = useCallback(async <T>(method: string, params: Record<string, any> = {}): Promise<T> => {
    const result = await rawMcpCall(method, params)
    return Array.isArray(result) ? result[0] : result
  }, [rawMcpCall])

  // Authenticated endpoints via MCP

  const createPoll = useCallback(async (data: CreatePollRequest): Promise<Poll> => {
    return mcpCall<Poll>('polling_upsert_poll', {
      title: data.title,
      description: data.description,
      datetime_start: data.datetime_start,
      datetime_end: data.datetime_end,
      slot_duration: data.slot_duration,
      closes_at: data.closes_at,
    })
  }, [mcpCall])

  const listPolls = useCallback(async (status?: PollStatus): Promise<Poll[]> => {
    const result = await mcpCall<Poll[]>('polling_list_polls', { status })
    return result || []
  }, [mcpCall])

  const getPoll = useCallback(async (pollId: number): Promise<PollResults> => {
    return mcpCall<PollResults>('polling_get_poll', { poll_id: pollId })
  }, [mcpCall])

  const updatePoll = useCallback(async (data: UpdatePollRequest): Promise<Poll> => {
    return mcpCall<Poll>('polling_upsert_poll', data)
  }, [mcpCall])

  const cancelPoll = useCallback(async (pollId: number): Promise<Poll> => {
    return mcpCall<Poll>('polling_upsert_poll', {
      poll_id: pollId,
      status: 'cancelled',
    })
  }, [mcpCall])

  const finalizePoll = useCallback(async (pollId: number, selectedTime: string): Promise<Poll> => {
    return mcpCall<Poll>('polling_upsert_poll', {
      poll_id: pollId,
      status: 'finalized',
      finalized_time: selectedTime,
    })
  }, [mcpCall])

  const closePoll = useCallback(async (pollId: number): Promise<Poll> => {
    return mcpCall<Poll>('polling_upsert_poll', {
      poll_id: pollId,
      status: 'closed',
    })
  }, [mcpCall])

  const deletePoll = useCallback(async (pollId: number): Promise<{ deleted: boolean; poll_id: number }> => {
    return mcpCall<{ deleted: boolean; poll_id: number }>('polling_delete_poll', {
      poll_id: pollId,
    })
  }, [mcpCall])

  return {
    createPoll,
    listPolls,
    getPoll,
    updatePoll,
    cancelPoll,
    finalizePoll,
    closePoll,
    deletePoll,
  }
}

// Public functions (no auth required) - exported separately
export const getPollBySlug = (slug: string): Promise<Poll> => {
  return publicFetch<Poll>(`/polls/respond/${slug}`)
}

export const submitPollResponse = (
  slug: string,
  data: SubmitResponseRequest
): Promise<SubmitResponseResult> => {
  return publicFetch<SubmitResponseResult>(`/polls/respond/${slug}`, {
    method: 'POST',
    body: JSON.stringify(data),
  })
}

export const updatePollResponse = (
  slug: string,
  responseId: number,
  editToken: string,
  data: SubmitResponseRequest
): Promise<{ status: string }> => {
  return publicFetch<{ status: string }>(
    `/polls/respond/${slug}/${responseId}`,
    {
      method: 'PUT',
      headers: { 'X-Edit-Token': editToken },
      body: JSON.stringify(data),
    }
  )
}

export interface ExistingResponse {
  response_id: number
  respondent_name: string | null
  respondent_email: string | null
  availabilities: AvailabilitySlot[]
}

export const getResponseByToken = (
  slug: string,
  editToken: string
): Promise<ExistingResponse> => {
  return publicFetch<ExistingResponse>(`/polls/respond/${slug}/response`, {
    headers: { 'X-Edit-Token': editToken },
  })
}

export const getPollResults = (slug: string): Promise<PollResults> => {
  return publicFetch<PollResults>(`/polls/respond/${slug}/results`)
}

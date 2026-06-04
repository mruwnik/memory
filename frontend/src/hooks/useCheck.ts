import { useCallback } from 'react'
import { useMCP } from './useMCP'

export type CheckMode = 'verify' | 'research' | 'link' | 'deep-dive' | 'investigation-team'
export type CheckStatus = 'queued' | 'in_flight' | 'ok' | 'error' | 'expired'

export interface CheckJob {
  job_id: string
  status: CheckStatus
  mode: CheckMode
  text: string
  result: Record<string, unknown> | null
  error: string | null
  submitted_at: string
  completed_at: string | null
}

export interface AskBody {
  text: string
  mode?: CheckMode
}

// Single page size for the questions list. The backend hard-clamps the list
// limit to 200 and there's no pagination, so this one constant drives the fetch
// size, the at-cap detection, and the "showing the N most recent" notice — keep
// it as the sole source of truth so those can't drift apart.
export const PAGE_LIMIT = 200

export const useCheck = () => {
  const { mcpCall } = useMCP()

  const listJobs = useCallback(async (): Promise<CheckJob[]> => {
    const result = await mcpCall('check_list_jobs', { limit: PAGE_LIMIT })
    return (result[0] as { jobs?: CheckJob[] })?.jobs ?? []
  }, [mcpCall])

  const ask = useCallback(async (body: AskBody): Promise<{ job_id: string; status: CheckStatus }> => {
    const result = await mcpCall('check_ask', {
      text: body.text,
      mode: body.mode ?? 'research',
    })
    return result[0] as { job_id: string; status: CheckStatus }
  }, [mcpCall])

  const deleteJob = useCallback(async (jobId: string): Promise<void> => {
    await mcpCall('check_delete', { job_id: jobId })
  }, [mcpCall])

  return { listJobs, ask, deleteJob }
}

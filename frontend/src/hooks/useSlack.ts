import { useCallback } from 'react'
import { useAuth } from './useAuth'

// Types for Slack Workspaces
export interface SlackWorkspace {
  id: string
  name: string
  domain: string | null
  collect_messages: boolean
  sync_interval_seconds: number
  last_sync_at: string | null
  sync_error: string | null
  channel_count: number
  user_count: number
}

export interface SlackWorkspaceUpdate {
  collect_messages?: boolean
  sync_interval_seconds?: number
}

// Types for Slack Channels
export interface SlackChannel {
  id: string
  workspace_id: string
  name: string
  channel_type: string
  is_private: boolean
  is_archived: boolean
  collect_messages: boolean | null
  effective_collect: boolean
  last_message_ts: string | null
}

export interface SlackChannelUpdate {
  collect_messages?: boolean | null
}

// Types for Slack Users
export interface SlackUser {
  id: string
  workspace_id: string
  username: string
  display_name: string | null
  real_name: string | null
  email: string | null
  is_bot: boolean
  system_user_id: number | null
  person_id: number | null
  person_identifier: string | null
}

export const useSlack = () => {
  const { apiCall } = useAuth()

  // === OAuth Operations ===

  const getAuthorizeUrl = useCallback(async (): Promise<{ authorization_url: string; state: string }> => {
    const response = await apiCall('/slack/authorize')
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to get authorization URL')
    }
    return response.json()
  }, [apiCall])

  // === Workspace Operations ===

  const listWorkspaces = useCallback(async (): Promise<SlackWorkspace[]> => {
    const response = await apiCall('/slack/workspaces')
    if (!response.ok) throw new Error('Failed to fetch Slack workspaces')
    return response.json()
  }, [apiCall])

  const getWorkspace = useCallback(async (workspaceId: string): Promise<SlackWorkspace> => {
    const response = await apiCall(`/slack/workspaces/${workspaceId}`)
    if (!response.ok) throw new Error('Failed to fetch workspace')
    return response.json()
  }, [apiCall])

  const updateWorkspace = useCallback(async (workspaceId: string, data: SlackWorkspaceUpdate): Promise<SlackWorkspace> => {
    const response = await apiCall(`/slack/workspaces/${workspaceId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to update workspace')
    }
    return response.json()
  }, [apiCall])

  const deleteWorkspace = useCallback(async (workspaceId: string): Promise<void> => {
    const response = await apiCall(`/slack/workspaces/${workspaceId}`, {
      method: 'DELETE',
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to disconnect workspace')
    }
  }, [apiCall])

  const triggerSync = useCallback(async (workspaceId: string): Promise<{ status: string }> => {
    const response = await apiCall(`/slack/workspaces/${workspaceId}/sync`, {
      method: 'POST',
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to trigger sync')
    }
    return response.json()
  }, [apiCall])

  // === Channel Operations ===

  const listChannels = useCallback(async (workspaceId: string, channelType?: string): Promise<SlackChannel[]> => {
    const url = channelType
      ? `/slack/workspaces/${workspaceId}/channels?channel_type=${channelType}`
      : `/slack/workspaces/${workspaceId}/channels`
    const response = await apiCall(url)
    if (!response.ok) throw new Error('Failed to fetch channels')
    return response.json()
  }, [apiCall])

  const updateChannel = useCallback(async (channelId: string, data: SlackChannelUpdate): Promise<SlackChannel> => {
    const response = await apiCall(`/slack/channels/${channelId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to update channel')
    }
    return response.json()
  }, [apiCall])

  // === User Operations ===

  const listUsers = useCallback(async (workspaceId: string, search?: string): Promise<SlackUser[]> => {
    const params = new URLSearchParams()
    if (search) params.set('search', search)
    const url = `/slack/workspaces/${workspaceId}/users?${params.toString()}`
    const response = await apiCall(url)
    if (!response.ok) throw new Error('Failed to fetch users')
    return response.json()
  }, [apiCall])

  return {
    // OAuth
    getAuthorizeUrl,
    // Workspaces
    listWorkspaces,
    getWorkspace,
    updateWorkspace,
    deleteWorkspace,
    triggerSync,
    // Channels
    listChannels,
    updateChannel,
    // Users
    listUsers,
  }
}

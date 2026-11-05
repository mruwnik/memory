import { useCallback } from 'react'
import { useAuth } from './useAuth'
import type { LLMKeyChoice, LLMKeyView } from './useSources'
import { useMCP } from './useMCP'
import { apiError } from './apiError'

// Types for Discord Bots
// Note: IDs are strings to avoid JavaScript precision loss on large Discord snowflake IDs
export interface DiscordBot {
  id: string
  application_id: string
  name: string
  is_active: boolean
  has_token: boolean
  realtime: DiscordRealtime
  public_key: string | null
  api_base_url: string
  // Where webhook delivery posts events
  events_url: string
  last_event_at: string | null
  // When the bot last listed its servers (gateway READY, a refresh, or the daily catch-up)
  last_sync_at: string | null
  created_at: string | null
  updated_at: string | null
  connected: boolean | null
}

// How a bot receives live events; every bot also reads and sends over REST
export type DiscordRealtime = 'gateway' | 'webhook'

export interface DiscordBotCreate {
  name: string
  token: string
  realtime?: DiscordRealtime
  application_id?: string
  public_key?: string
  api_base_url?: string
}

export interface DiscordBotUpdate {
  name?: string
  is_active?: boolean
  token?: string
  realtime?: DiscordRealtime
  public_key?: string
  api_base_url?: string
}

// Types for Discord Servers
export interface DiscordServer {
  id: string
  name: string
  description: string | null
  member_count: number | null
  collect_messages: boolean
  last_sync_at: string | null
  channel_count: number
  // The requester's bots in this server
  bot_ids: string[]
  // Access control
  project_id: number | null
  sensitivity: 'public' | 'basic' | 'internal' | 'confidential'
  llm_key: LLMKeyView
}

export interface DiscordServerUpdate {
  collect_messages?: boolean
  // Access control
  project_id?: number | null
  sensitivity?: 'public' | 'basic' | 'internal' | 'confidential'
  llm_key?: LLMKeyChoice
}

// Types for Discord Channels
export interface DiscordChannel {
  id: string
  server_id: string | null
  server_name: string | null
  name: string
  channel_type: string
  collect_messages: boolean | null
  effective_collect: boolean
  // Access control
  project_id: number | null
  sensitivity: 'public' | 'basic' | 'internal' | 'confidential'
}

export interface DiscordChannelUpdate {
  collect_messages?: boolean | null
  // Access control
  project_id?: number | null
  sensitivity?: 'public' | 'basic' | 'internal' | 'confidential'
}

// Types for Bot Users

// MCP list_channels response type
interface MCPChannelResponse {
  project_id: number | null
  sensitivity: DiscordChannel["sensitivity"]
  id: string | number  // MCP may return either
  name: string
  type: string
  server_id: string | number | null
  collect_messages: boolean | null
  effective_collect: boolean
}

const CHANNEL_FIELDS = ['id', 'name', 'type', 'server_id', 'collect_messages', 'effective_collect', 'project_id', 'sensitivity']

export const useDiscord = () => {
  const { apiCall } = useAuth()
  const { mcpCall } = useMCP()

  // === Bot Operations ===

  const listBots = useCallback(async (userId?: number): Promise<DiscordBot[]> => {
    const params = userId !== undefined ? `?user_id=${userId}` : ''
    const response = await apiCall(`/discord/bots${params}`)
    if (!response.ok) throw await apiError(response, 'Failed to fetch Discord bots')
    return response.json()
  }, [apiCall])

  const createBot = useCallback(async (data: DiscordBotCreate): Promise<DiscordBot> => {
    const response = await apiCall('/discord/bots', {
      method: 'POST',
      body: JSON.stringify(data),
    })
    if (!response.ok) throw await apiError(response, 'Failed to create Discord bot')
    return response.json()
  }, [apiCall])

  const updateBot = useCallback(async (botId: string, data: DiscordBotUpdate): Promise<DiscordBot> => {
    const response = await apiCall(`/discord/bots/${botId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) throw await apiError(response, 'Failed to update Discord bot')
    return response.json()
  }, [apiCall])

  const deleteBot = useCallback(async (botId: string): Promise<void> => {
    const response = await apiCall(`/discord/bots/${botId}`, {
      method: 'DELETE',
    })
    if (!response.ok) throw await apiError(response, 'Failed to delete Discord bot')
  }, [apiCall])

  const getBotHealth = useCallback(async (botId: string): Promise<{ bot_id: string; connected: boolean }> => {
    const response = await apiCall(`/discord/bots/${botId}/health`)
    if (!response.ok) throw await apiError(response, 'Failed to get bot health')
    return response.json()
  }, [apiCall])

  const refreshBotMetadata = useCallback(async (botId: string): Promise<{ success: boolean }> => {
    const response = await apiCall(`/discord/bots/${botId}/refresh`, {
      method: 'POST',
    })
    if (!response.ok) throw await apiError(response, 'Failed to refresh metadata')
    return response.json()
  }, [apiCall])

  const getBotInviteUrl = useCallback(async (botId: string): Promise<{ invite_url: string }> => {
    const response = await apiCall(`/discord/bots/${botId}/invite`)
    if (!response.ok) throw await apiError(response, 'Failed to get bot invite URL')
    return response.json()
  }, [apiCall])

  // === Server Operations ===

  const listServers = useCallback(async (): Promise<DiscordServer[]> => {
    const response = await apiCall('/discord/servers')
    if (!response.ok) throw await apiError(response, 'Failed to fetch Discord servers')
    return response.json()
  }, [apiCall])

  const updateServer = useCallback(async (serverId: string, data: DiscordServerUpdate): Promise<DiscordServer> => {
    const response = await apiCall(`/discord/servers/${serverId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) throw await apiError(response, 'Failed to update Discord server')
    return response.json()
  }, [apiCall])

  // === Channel Operations (using MCP for list, REST for update) ===

  const listChannels = useCallback(async (serverId?: string, serverName?: string): Promise<DiscordChannel[]> => {
    // Use MCP tool for listing channels
    const params: Record<string, unknown> = { fields: CHANNEL_FIELDS }
    const server = serverId || serverName
    if (server) params.server = server

    const result = await mcpCall('discord_list_channels', params)

    // Validate MCP response format
    const response = result[0]
    if (!response || !('channels' in response)) {
      console.error('Unexpected MCP response format for discord_list_channels:', result)
      return []
    }

    // Transform MCP response to our DiscordChannel type
    const mcpChannels: MCPChannelResponse[] = response.channels || []
    return mcpChannels.map((ch: MCPChannelResponse) => ({
      id: String(ch.id),
      server_id: ch.server_id != null ? String(ch.server_id) : null,
      server_name: null, // MCP doesn't return this
      name: ch.name,
      channel_type: ch.type,
      collect_messages: ch.collect_messages,
      project_id: ch.project_id,
      sensitivity: ch.sensitivity,
      effective_collect: ch.effective_collect,
    }))
  }, [mcpCall])

  const updateChannel = useCallback(async (channelId: string, data: DiscordChannelUpdate): Promise<DiscordChannel> => {
    const response = await apiCall(`/discord/channels/${channelId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) throw await apiError(response, 'Failed to update Discord channel')
    return response.json()
  }, [apiCall])

  return {
    // Bots
    listBots,
    createBot,
    updateBot,
    deleteBot,
    getBotHealth,
    refreshBotMetadata,
    getBotInviteUrl,
    // Bot Users
    // Servers
    listServers,
    updateServer,
    // Channels
    listChannels,
    updateChannel,
  }
}

import { useCallback } from 'react'
import { useAuth } from './useAuth'
import { useMCP } from './useMCP'

// Types for Discord Bots
export interface DiscordBot {
  id: number
  name: string
  is_active: boolean
  created_at: string | null
  updated_at: string | null
  connected: boolean | null
}

export interface DiscordBotCreate {
  name: string
  token: string
}

export interface DiscordBotUpdate {
  name?: string
  is_active?: boolean
}

// Types for Discord Servers
export interface DiscordServer {
  id: number
  name: string
  description: string | null
  member_count: number | null
  collect_messages: boolean
  last_sync_at: string | null
  channel_count: number
}

export interface DiscordServerUpdate {
  collect_messages?: boolean
}

// Types for Discord Channels
export interface DiscordChannel {
  id: number
  server_id: number | null
  server_name: string | null
  name: string
  channel_type: string
  collect_messages: boolean | null
  effective_collect: boolean
}

export interface DiscordChannelUpdate {
  collect_messages?: boolean | null
}

// MCP list_channels response type
interface MCPChannelResponse {
  id: number
  name: string
  type: string
  server_id: number | null
  collect_messages: boolean
}

export const useDiscord = () => {
  const { apiCall } = useAuth()
  const { mcpCall } = useMCP()

  // === Bot Operations ===

  const listBots = useCallback(async (): Promise<DiscordBot[]> => {
    const response = await apiCall('/discord/bots')
    if (!response.ok) throw new Error('Failed to fetch Discord bots')
    return response.json()
  }, [apiCall])

  const createBot = useCallback(async (data: DiscordBotCreate): Promise<DiscordBot> => {
    const response = await apiCall('/discord/bots', {
      method: 'POST',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to create Discord bot')
    }
    return response.json()
  }, [apiCall])

  const updateBot = useCallback(async (botId: number, data: DiscordBotUpdate): Promise<DiscordBot> => {
    const response = await apiCall(`/discord/bots/${botId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to update Discord bot')
    }
    return response.json()
  }, [apiCall])

  const deleteBot = useCallback(async (botId: number): Promise<void> => {
    const response = await apiCall(`/discord/bots/${botId}`, {
      method: 'DELETE',
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to delete Discord bot')
    }
  }, [apiCall])

  const getBotHealth = useCallback(async (botId: number): Promise<{ bot_id: number; connected: boolean }> => {
    const response = await apiCall(`/discord/bots/${botId}/health`)
    if (!response.ok) throw new Error('Failed to get bot health')
    return response.json()
  }, [apiCall])

  const refreshBotMetadata = useCallback(async (botId: number): Promise<{ success: boolean }> => {
    const response = await apiCall(`/discord/bots/${botId}/refresh`, {
      method: 'POST',
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to refresh metadata')
    }
    return response.json()
  }, [apiCall])

  // === Server Operations ===

  const listServers = useCallback(async (botId?: number): Promise<DiscordServer[]> => {
    const url = botId ? `/discord/servers?bot_id=${botId}` : '/discord/servers'
    const response = await apiCall(url)
    if (!response.ok) throw new Error('Failed to fetch Discord servers')
    return response.json()
  }, [apiCall])

  const updateServer = useCallback(async (serverId: number, data: DiscordServerUpdate): Promise<DiscordServer> => {
    const response = await apiCall(`/discord/servers/${serverId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to update Discord server')
    }
    return response.json()
  }, [apiCall])

  // === Channel Operations (using MCP for list, REST for update) ===

  const listChannels = useCallback(async (serverId?: number, serverName?: string): Promise<DiscordChannel[]> => {
    // Use MCP tool for listing channels
    const params: Record<string, unknown> = {}
    if (serverId) params.server_id = serverId
    if (serverName) params.server_name = serverName

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
      id: ch.id,
      server_id: ch.server_id,
      server_name: null, // MCP doesn't return this
      name: ch.name,
      channel_type: ch.type,
      collect_messages: ch.collect_messages,
      effective_collect: ch.collect_messages, // MCP returns the resolved value
    }))
  }, [mcpCall])

  const updateChannel = useCallback(async (channelId: number, data: DiscordChannelUpdate): Promise<DiscordChannel> => {
    const response = await apiCall(`/discord/channels/${channelId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to update Discord channel')
    }
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
    // Servers
    listServers,
    updateServer,
    // Channels
    listChannels,
    updateChannel,
  }
}

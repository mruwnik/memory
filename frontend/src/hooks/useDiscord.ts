import { useCallback } from 'react'
import { useAuth } from './useAuth'
import { useMCP } from './useMCP'

// Types for Discord Bots
// Note: IDs are strings to avoid JavaScript precision loss on large Discord snowflake IDs
export interface DiscordBot {
  id: string
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
  id: string
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
  id: string
  server_id: string | null
  server_name: string | null
  name: string
  channel_type: string
  collect_messages: boolean | null
  effective_collect: boolean
}

export interface DiscordChannelUpdate {
  collect_messages?: boolean | null
}

// Types for Bot Users
export interface BotUser {
  id: number
  name: string
}

// MCP list_channels response type
interface MCPChannelResponse {
  id: string | number  // MCP may return either
  name: string
  type: string
  server_id: string | number | null
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

  const updateBot = useCallback(async (botId: string, data: DiscordBotUpdate): Promise<DiscordBot> => {
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

  const deleteBot = useCallback(async (botId: string): Promise<void> => {
    const response = await apiCall(`/discord/bots/${botId}`, {
      method: 'DELETE',
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to delete Discord bot')
    }
  }, [apiCall])

  const getBotHealth = useCallback(async (botId: string): Promise<{ bot_id: string; connected: boolean }> => {
    const response = await apiCall(`/discord/bots/${botId}/health`)
    if (!response.ok) throw new Error('Failed to get bot health')
    return response.json()
  }, [apiCall])

  const refreshBotMetadata = useCallback(async (botId: string): Promise<{ success: boolean }> => {
    const response = await apiCall(`/discord/bots/${botId}/refresh`, {
      method: 'POST',
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to refresh metadata')
    }
    return response.json()
  }, [apiCall])

  const getBotInviteUrl = useCallback(async (botId: string): Promise<{ invite_url: string }> => {
    const response = await apiCall(`/discord/bots/${botId}/invite`)
    if (!response.ok) throw new Error('Failed to get bot invite URL')
    return response.json()
  }, [apiCall])

  // === Bot User Operations ===

  const listBotUsers = useCallback(async (botId: string): Promise<BotUser[]> => {
    const response = await apiCall(`/discord/bots/${botId}/users`)
    if (!response.ok) throw new Error('Failed to fetch bot users')
    return response.json()
  }, [apiCall])

  const addBotUser = useCallback(async (botId: string, userId: number): Promise<{ status: string; user_id: number }> => {
    const response = await apiCall(`/discord/bots/${botId}/users`, {
      method: 'POST',
      body: JSON.stringify({ user_id: userId }),
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to add user to bot')
    }
    return response.json()
  }, [apiCall])

  const removeBotUser = useCallback(async (botId: string, userId: number): Promise<{ status: string; user_id: number }> => {
    const response = await apiCall(`/discord/bots/${botId}/users/${userId}`, {
      method: 'DELETE',
    })
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || 'Failed to remove user from bot')
    }
    return response.json()
  }, [apiCall])

  // === Server Operations ===

  const listServers = useCallback(async (botId?: string): Promise<DiscordServer[]> => {
    const url = botId ? `/discord/servers?bot_id=${botId}` : '/discord/servers'
    const response = await apiCall(url)
    if (!response.ok) throw new Error('Failed to fetch Discord servers')
    return response.json()
  }, [apiCall])

  const updateServer = useCallback(async (serverId: string, data: DiscordServerUpdate): Promise<DiscordServer> => {
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

  const listChannels = useCallback(async (serverId?: string, serverName?: string): Promise<DiscordChannel[]> => {
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
      id: String(ch.id),
      server_id: ch.server_id != null ? String(ch.server_id) : null,
      server_name: null, // MCP doesn't return this
      name: ch.name,
      channel_type: ch.type,
      collect_messages: ch.collect_messages,
      effective_collect: ch.collect_messages, // MCP returns the resolved value
    }))
  }, [mcpCall])

  const updateChannel = useCallback(async (channelId: string, data: DiscordChannelUpdate): Promise<DiscordChannel> => {
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
    getBotInviteUrl,
    // Bot Users
    listBotUsers,
    addBotUser,
    removeBotUser,
    // Servers
    listServers,
    updateServer,
    // Channels
    listChannels,
    updateChannel,
  }
}

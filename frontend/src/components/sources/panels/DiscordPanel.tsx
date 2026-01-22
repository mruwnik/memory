import { useState, useEffect, useCallback } from 'react'
import { useDiscord, DiscordBot, DiscordServer, DiscordChannel, BotUser } from '@/hooks/useDiscord'
import { useUsers, User } from '@/hooks/useUsers'
import {
  Modal,
  EmptyState,
  LoadingState,
  ErrorState,
  StatusBadge,
  SyncStatus,
  SyncButton,
  ConfirmDialog,
} from '../shared'
import { styles, cx } from '../styles'

export const DiscordPanel = () => {
  const {
    listBots,
    createBot,
    updateBot,
    deleteBot,
    refreshBotMetadata,
    getBotInviteUrl,
    listServers,
    updateServer,
    listChannels,
    updateChannel,
    listBotUsers,
    addBotUser,
    removeBotUser,
  } = useDiscord()
  const { listUsers } = useUsers()

  const [bots, setBots] = useState<DiscordBot[]>([])
  const [servers, setServers] = useState<DiscordServer[]>([])
  const [channelsByServer, setChannelsByServer] = useState<Record<string, DiscordChannel[]>>({})
  const [expandedBot, setExpandedBot] = useState<string | null>(null)
  const [expandedServer, setExpandedServer] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [addingBot, setAddingBot] = useState(false)
  const [deletingBot, setDeletingBot] = useState<DiscordBot | null>(null)
  const [usersByBot, setUsersByBot] = useState<Record<string, BotUser[]>>({})
  const [allUsers, setAllUsers] = useState<User[]>([])
  const [managingUsersBot, setManagingUsersBot] = useState<DiscordBot | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const botsData = await listBots()
      setBots(botsData)

      // If user has bots, load servers
      if (botsData.length > 0) {
        const serversData = await listServers()
        setServers(serversData)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load data')
    } finally {
      setLoading(false)
    }
  }, [listBots, listServers])

  useEffect(() => {
    loadData()
  }, [loadData])

  const loadChannels = async (serverId: string) => {
    if (channelsByServer[serverId]) return // Already loaded

    try {
      const channels = await listChannels(serverId)
      setChannelsByServer(prev => ({ ...prev, [serverId]: channels }))
    } catch (e) {
      console.error('Failed to load channels:', e)
    }
  }

  const handleAddBot = async (data: { name: string; token: string }) => {
    await createBot(data)
    setAddingBot(false)
    loadData()
  }

  const handleDeleteBot = async (bot: DiscordBot) => {
    await deleteBot(bot.id)
    setDeletingBot(null)
    loadData()
  }

  const handleToggleBotActive = async (bot: DiscordBot) => {
    await updateBot(bot.id, { is_active: !bot.is_active })
    loadData()
  }

  const handleRefreshMetadata = async (botId: string) => {
    await refreshBotMetadata(botId)
    // Reload servers after metadata refresh
    const serversData = await listServers()
    setServers(serversData)
    // Clear cached channels to force reload
    setChannelsByServer({})
  }

  const handleGetInviteUrl = async (botId: string) => {
    const result = await getBotInviteUrl(botId)
    window.open(result.invite_url, '_blank')
  }

  const loadBotUsers = async (botId: string) => {
    if (usersByBot[botId]) return usersByBot[botId]
    try {
      const users = await listBotUsers(botId)
      setUsersByBot(prev => ({ ...prev, [botId]: users }))
      return users
    } catch (e) {
      console.error('Failed to load bot users:', e)
      return []
    }
  }

  const handleManageUsers = async (bot: DiscordBot) => {
    // Load users for this bot and all system users
    await loadBotUsers(bot.id)
    try {
      const users = await listUsers()
      setAllUsers(users)
    } catch (e) {
      console.error('Failed to load users:', e)
    }
    setManagingUsersBot(bot)
  }

  const handleAddUserToBot = async (botId: string, userId: number) => {
    await addBotUser(botId, userId)
    const users = await listBotUsers(botId)
    setUsersByBot(prev => ({ ...prev, [botId]: users }))
  }

  const handleRemoveUserFromBot = async (botId: string, userId: number) => {
    await removeBotUser(botId, userId)
    const users = await listBotUsers(botId)
    setUsersByBot(prev => ({ ...prev, [botId]: users }))
  }

  const handleToggleServerCollect = async (server: DiscordServer) => {
    await updateServer(server.id, { collect_messages: !server.collect_messages })
    // Refresh servers
    const serversData = await listServers()
    setServers(serversData)
    // Refresh channels for this server (inheritance may have changed)
    if (channelsByServer[server.id]) {
      const channels = await listChannels(server.id)
      setChannelsByServer(prev => ({ ...prev, [server.id]: channels }))
    }
  }

  const handleToggleChannelCollect = async (channel: DiscordChannel) => {
    // Cycle through: inherit (null) -> on (true) -> off (false) -> inherit (null)
    let newValue: boolean | null
    if (channel.collect_messages === null) {
      newValue = true
    } else if (channel.collect_messages === true) {
      newValue = false
    } else {
      newValue = null
    }

    const updatedChannel = await updateChannel(channel.id, { collect_messages: newValue })

    // Refresh channels for this server, or update single channel for DMs
    if (channel.server_id) {
      const channels = await listChannels(channel.server_id)
      setChannelsByServer(prev => ({ ...prev, [channel.server_id!]: channels }))
    } else {
      // For DM channels (no server_id), update the channel in place
      // DM channels are stored under a special key
      // Since DMs aren't currently shown in this UI, this is a defensive update
      setChannelsByServer(prev => {
        const updated = { ...prev }
        for (const serverId of Object.keys(updated)) {
          updated[serverId] = updated[serverId].map(ch =>
            ch.id === channel.id ? updatedChannel : ch
          )
        }
        return updated
      })
    }
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadData} />

  return (
    <div className={styles.panel}>
      <div className={styles.panelHeader}>
        <h3 className={styles.panelTitle}>Discord</h3>
        <button className={styles.btnAdd} onClick={() => setAddingBot(true)}>
          Add Bot
        </button>
      </div>

      {bots.length === 0 ? (
        <EmptyState
          message="No Discord bots configured. Add a bot to start collecting messages."
          actionLabel="Add Bot"
          onAction={() => setAddingBot(true)}
        />
      ) : (
        <div className={styles.sourceList}>
          {bots.map(bot => (
            <BotCard
              key={bot.id}
              bot={bot}
              servers={servers}
              channelsByServer={channelsByServer}
              expanded={expandedBot === bot.id}
              expandedServer={expandedServer}
              onToggleExpand={() => setExpandedBot(expandedBot === bot.id ? null : bot.id)}
              onExpandServer={(serverId) => {
                setExpandedServer(expandedServer === serverId ? null : serverId)
                if (expandedServer !== serverId) {
                  loadChannels(serverId)
                }
              }}
              onToggleActive={() => handleToggleBotActive(bot)}
              onRefresh={() => handleRefreshMetadata(bot.id)}
              onDelete={() => setDeletingBot(bot)}
              onToggleServerCollect={handleToggleServerCollect}
              onToggleChannelCollect={handleToggleChannelCollect}
              onGetInviteUrl={() => handleGetInviteUrl(bot.id)}
              onManageUsers={() => handleManageUsers(bot)}
            />
          ))}
        </div>
      )}

      {addingBot && (
        <AddBotForm
          onAdd={handleAddBot}
          onCancel={() => setAddingBot(false)}
        />
      )}

      {deletingBot && (
        <ConfirmDialog
          message={`Are you sure you want to remove "${deletingBot.name}"? ${
            'This will revoke your access to this bot.'
          }`}
          onConfirm={() => handleDeleteBot(deletingBot)}
          onCancel={() => setDeletingBot(null)}
        />
      )}

      {managingUsersBot && (
        <ManageUsersModal
          bot={managingUsersBot}
          botUsers={usersByBot[managingUsersBot.id] || []}
          allUsers={allUsers}
          onAddUser={(userId) => handleAddUserToBot(managingUsersBot.id, userId)}
          onRemoveUser={(userId) => handleRemoveUserFromBot(managingUsersBot.id, userId)}
          onClose={() => setManagingUsersBot(null)}
        />
      )}
    </div>
  )
}

interface BotCardProps {
  bot: DiscordBot
  servers: DiscordServer[]
  channelsByServer: Record<string, DiscordChannel[]>
  expanded: boolean
  expandedServer: string | null
  onToggleExpand: () => void
  onExpandServer: (serverId: string) => void
  onToggleActive: () => void
  onRefresh: () => Promise<void>
  onDelete: () => void
  onToggleServerCollect: (server: DiscordServer) => Promise<void>
  onToggleChannelCollect: (channel: DiscordChannel) => Promise<void>
  onGetInviteUrl: () => Promise<void>
  onManageUsers: () => Promise<void>
}

const BotCard = ({
  bot,
  servers,
  channelsByServer,
  expanded,
  expandedServer,
  onToggleExpand,
  onExpandServer,
  onToggleActive,
  onRefresh,
  onDelete,
  onToggleServerCollect,
  onToggleChannelCollect,
  onGetInviteUrl,
  onManageUsers,
}: BotCardProps) => {
  return (
    <div className="border border-slate-200 rounded-lg p-4">
      <div className={styles.cardHeader}>
        <div className={styles.cardInfo}>
          <h4 className={styles.cardTitle}>{bot.name}</h4>
          <div className="flex items-center gap-2 text-sm text-slate-500 mt-1">
            <span className={cx(
              'w-2 h-2 rounded-full',
              bot.connected ? 'bg-green-500' : 'bg-red-500'
            )} />
            <span>{bot.connected ? 'Connected' : 'Disconnected'}</span>
            {bot.updated_at && (
              <>
                <span className="text-slate-300">|</span>
                <SyncStatus lastSyncAt={bot.updated_at} />
              </>
            )}
          </div>
        </div>
        <div className={styles.cardActions}>
          <StatusBadge active={bot.is_active} onClick={onToggleActive} />
        </div>
      </div>

      <div className="flex gap-2 mt-3">
        <SyncButton
          onSync={onRefresh}
          disabled={!bot.is_active}
          label="Refresh Metadata"
        />
        <button
          className="px-3 py-1.5 text-sm rounded bg-indigo-100 text-indigo-700 hover:bg-indigo-200"
          onClick={onGetInviteUrl}
        >
          Add to Server
        </button>
        <button
          className="px-3 py-1.5 text-sm rounded bg-slate-100 text-slate-700 hover:bg-slate-200"
          onClick={onManageUsers}
        >
          Manage Users
        </button>
        <button className={styles.btnDelete} onClick={onDelete}>
          Remove
        </button>
      </div>

      {/* Servers section */}
      <div className="mt-4 pt-4 border-t border-slate-100">
        <button
          className="flex items-center gap-2 text-sm font-medium text-slate-700 hover:text-slate-900"
          onClick={onToggleExpand}
        >
          <span className={cx('transition-transform', expanded && 'rotate-90')}>
            ▶
          </span>
          Servers ({servers.length})
        </button>

        {expanded && servers.length === 0 && (
          <p className="text-sm text-slate-400 italic mt-2 ml-5">
            No servers found. Click "Refresh Metadata" to sync.
          </p>
        )}

        {expanded && servers.length > 0 && (
          <div className="mt-2 ml-5 space-y-2">
            {servers.map(server => (
              <ServerCard
                key={server.id}
                server={server}
                channels={channelsByServer[server.id] || []}
                expanded={expandedServer === server.id}
                onToggleExpand={() => onExpandServer(server.id)}
                onToggleCollect={() => onToggleServerCollect(server)}
                onToggleChannelCollect={onToggleChannelCollect}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

interface ServerCardProps {
  server: DiscordServer
  channels: DiscordChannel[]
  expanded: boolean
  onToggleExpand: () => void
  onToggleCollect: () => Promise<void>
  onToggleChannelCollect: (channel: DiscordChannel) => Promise<void>
}

const ServerCard = ({
  server,
  channels,
  expanded,
  onToggleExpand,
  onToggleCollect,
  onToggleChannelCollect,
}: ServerCardProps) => {
  const [toggling, setToggling] = useState(false)

  const handleToggle = async () => {
    setToggling(true)
    try {
      await onToggleCollect()
    } finally {
      setToggling(false)
    }
  }

  return (
    <div className="border border-slate-200 rounded p-3 bg-white">
      <div className="flex items-center justify-between">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-slate-800">{server.name}</span>
            {server.member_count && (
              <span className="text-xs text-slate-500">
                {server.member_count} members
              </span>
            )}
          </div>
          <div className="text-xs text-slate-500">
            {server.channel_count} channels
          </div>
        </div>
        <button
          className={cx(
            'px-3 py-1 rounded text-sm',
            server.collect_messages
              ? 'bg-green-100 text-green-700 hover:bg-green-200'
              : 'bg-slate-100 text-slate-600 hover:bg-slate-200',
            toggling && 'opacity-50'
          )}
          onClick={handleToggle}
          disabled={toggling}
        >
          {server.collect_messages ? 'Collecting' : 'Not Collecting'}
        </button>
      </div>

      {/* Channels section */}
      <div className="mt-2 pt-2 border-t border-slate-100">
        <button
          className="flex items-center gap-2 text-xs font-medium text-slate-600 hover:text-slate-800"
          onClick={onToggleExpand}
        >
          <span className={cx('transition-transform', expanded && 'rotate-90')}>
            ▶
          </span>
          Channels
        </button>

        {expanded && (
          <div className="mt-2 space-y-1">
            {channels.length === 0 ? (
              <p className="text-xs text-slate-400 italic ml-4">Loading...</p>
            ) : (
              channels.map(channel => (
                <ChannelRow
                  key={channel.id}
                  channel={channel}
                  serverCollecting={server.collect_messages}
                  onToggle={() => onToggleChannelCollect(channel)}
                />
              ))
            )}
          </div>
        )}
      </div>
    </div>
  )
}

interface ChannelRowProps {
  channel: DiscordChannel
  serverCollecting: boolean
  onToggle: () => Promise<void>
}

const ChannelRow = ({ channel, serverCollecting, onToggle }: ChannelRowProps) => {
  const [toggling, setToggling] = useState(false)

  const handleToggle = async () => {
    setToggling(true)
    try {
      await onToggle()
    } finally {
      setToggling(false)
    }
  }

  // Determine display state
  const isInheriting = channel.collect_messages === null
  const effectivelyCollecting = channel.effective_collect

  // Channel type icons
  const typeIcon = {
    text: '#',
    voice: '🔊',
    dm: '💬',
    group_dm: '👥',
    thread: '🧵',
  }[channel.channel_type] || '#'

  return (
    <div className="flex items-center justify-between py-1 px-2 ml-4 rounded hover:bg-slate-50">
      <div className="flex items-center gap-2 text-sm">
        <span className="text-slate-400">{typeIcon}</span>
        <span className="text-slate-700">{channel.name}</span>
      </div>
      <button
        className={cx(
          'px-2 py-0.5 rounded text-xs flex items-center gap-1',
          toggling && 'opacity-50',
          // Colors based on state
          isInheriting
            ? effectivelyCollecting
              ? 'bg-blue-50 text-blue-600 hover:bg-blue-100'
              : 'bg-slate-50 text-slate-500 hover:bg-slate-100'
            : channel.collect_messages
              ? 'bg-green-100 text-green-700 hover:bg-green-200'
              : 'bg-red-50 text-red-600 hover:bg-red-100'
        )}
        onClick={handleToggle}
        disabled={toggling}
        title={
          isInheriting
            ? `Inheriting from server (${serverCollecting ? 'collecting' : 'not collecting'})`
            : channel.collect_messages
              ? 'Explicitly collecting'
              : 'Explicitly not collecting'
        }
      >
        {/* Status indicator */}
        <span className={cx(
          'w-2 h-2 rounded-full',
          isInheriting ? 'border border-current' : 'bg-current'
        )} />
        {isInheriting
          ? `Inherit (${effectivelyCollecting ? 'yes' : 'no'})`
          : channel.collect_messages
            ? 'Collecting'
            : 'Skipping'}
      </button>
    </div>
  )
}

interface AddBotFormProps {
  onAdd: (data: { name: string; token: string }) => Promise<void>
  onCancel: () => void
}

const AddBotForm = ({ onAdd, onCancel }: AddBotFormProps) => {
  const [name, setName] = useState('')
  const [token, setToken] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || !token.trim()) {
      setError('Please fill in all fields')
      return
    }

    setSubmitting(true)
    setError(null)

    try {
      await onAdd({ name: name.trim(), token: token.trim() })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to add bot')
      setSubmitting(false)
    }
  }

  return (
    <Modal title="Add Discord Bot" onClose={onCancel}>
      <form onSubmit={handleSubmit} className={styles.form}>
        {error && <div className={styles.formError}>{error}</div>}

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Bot Name</label>
          <input
            type="text"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="e.g., My Bot"
            required
            className={styles.formInput}
          />
          <p className={styles.formHint}>A friendly name for this bot</p>
        </div>

        <div className={styles.formGroup}>
          <label className={styles.formLabel}>Bot Token</label>
          <input
            type="password"
            value={token}
            onChange={e => setToken(e.target.value)}
            placeholder="Paste your Discord bot token"
            required
            className={styles.formInput}
          />
          <p className={styles.formHint}>
            Get this from the Discord Developer Portal under Bot → Token
          </p>
        </div>

        <div className={styles.formActions}>
          <button
            type="button"
            className={styles.btnCancel}
            onClick={onCancel}
            disabled={submitting}
          >
            Cancel
          </button>
          <button
            type="submit"
            className={styles.btnSubmit}
            disabled={submitting}
          >
            {submitting ? 'Adding...' : 'Add Bot'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

interface ManageUsersModalProps {
  bot: DiscordBot
  botUsers: BotUser[]
  allUsers: User[]
  onAddUser: (userId: number) => Promise<void>
  onRemoveUser: (userId: number) => Promise<void>
  onClose: () => void
}

const ManageUsersModal = ({
  bot,
  botUsers,
  allUsers,
  onAddUser,
  onRemoveUser,
  onClose,
}: ManageUsersModalProps) => {
  const [adding, setAdding] = useState<number | null>(null)
  const [removing, setRemoving] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const authorizedUserIds = new Set(botUsers.map(u => u.id))
  const availableUsers = allUsers.filter(u => !authorizedUserIds.has(u.id))

  const handleAdd = async (userId: number) => {
    setAdding(userId)
    setError(null)
    try {
      await onAddUser(userId)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to add user')
    } finally {
      setAdding(null)
    }
  }

  const handleRemove = async (userId: number) => {
    setRemoving(userId)
    setError(null)
    try {
      await onRemoveUser(userId)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to remove user')
    } finally {
      setRemoving(null)
    }
  }

  return (
    <Modal title={`Manage Users - ${bot.name}`} onClose={onClose}>
      <div className="space-y-4">
        {error && <div className={styles.formError}>{error}</div>}

        {/* Current authorized users */}
        <div>
          <h4 className="text-sm font-medium text-slate-700 mb-2">Authorized Users</h4>
          {botUsers.length === 0 ? (
            <p className="text-sm text-slate-500 italic">No users authorized</p>
          ) : (
            <div className="space-y-2">
              {botUsers.map(user => (
                <div
                  key={user.id}
                  className="flex items-center justify-between p-2 bg-slate-50 rounded"
                >
                  <span className="text-sm text-slate-800">{user.name}</span>
                  <button
                    className={cx(
                      'px-2 py-1 text-xs rounded',
                      removing === user.id
                        ? 'bg-slate-200 text-slate-500'
                        : 'bg-red-100 text-red-700 hover:bg-red-200'
                    )}
                    onClick={() => handleRemove(user.id)}
                    disabled={removing === user.id || botUsers.length === 1}
                    title={botUsers.length === 1 ? 'Cannot remove the last user' : 'Remove user'}
                  >
                    {removing === user.id ? 'Removing...' : 'Remove'}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Add new user */}
        {availableUsers.length > 0 && (
          <div>
            <h4 className="text-sm font-medium text-slate-700 mb-2">Add User</h4>
            <div className="space-y-2">
              {availableUsers.map(user => (
                <div
                  key={user.id}
                  className="flex items-center justify-between p-2 bg-slate-50 rounded"
                >
                  <div>
                    <span className="text-sm text-slate-800">{user.name}</span>
                    <span className="text-xs text-slate-500 ml-2">{user.email}</span>
                  </div>
                  <button
                    className={cx(
                      'px-2 py-1 text-xs rounded',
                      adding === user.id
                        ? 'bg-slate-200 text-slate-500'
                        : 'bg-green-100 text-green-700 hover:bg-green-200'
                    )}
                    onClick={() => handleAdd(user.id)}
                    disabled={adding === user.id}
                  >
                    {adding === user.id ? 'Adding...' : 'Add'}
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="pt-4 border-t border-slate-200">
          <button
            className="w-full px-4 py-2 text-sm rounded bg-slate-100 text-slate-700 hover:bg-slate-200"
            onClick={onClose}
          >
            Done
          </button>
        </div>
      </div>
    </Modal>
  )
}

export default DiscordPanel

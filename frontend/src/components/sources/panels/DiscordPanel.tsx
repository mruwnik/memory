import { useState, useEffect, useCallback } from 'react'
import { useDiscord, DiscordBot, DiscordServer, DiscordChannel, DiscordChannelUpdate, DiscordServerUpdate } from '@/hooks/useDiscord'
import { useModule } from '@/hooks/useModule'
import { useProjects, Project } from '@/hooks/useProjects'
import { AccessSelects, CollectSelect, PauseButton, PopupBlockedNotice, StatusBadge, SyncButton, formatRelativeTime, lazySubList, openPopup, useBusy } from '../shared'
import { LLMKeyField } from '../LLMKeyField'
import { Badge, Button, ConfirmButton, EmptyState, ErrorState, ExpandToggle, Field, FormActions, Input, List, LoadingState, Modal, Notice, Row, RowError, Section, SubList, cx, useActionNotice, useSubmit } from '@/components/shared/ui'
import { useSourcesContext } from '../context'

export const DiscordPanel = () => {
  const { userId, refreshCounts } = useSourcesContext()
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
  } = useDiscord()
  const { listProjects } = useProjects()
  const teamsOn = useModule('teams')

  const [bots, setBots] = useState<DiscordBot[]>([])
  const [servers, setServers] = useState<DiscordServer[]>([])
  const [channelsByServer, setChannelsByServer] = useState<Record<string, DiscordChannel[] | Error>>({})
  const [projects, setProjects] = useState<Project[]>([])
  const [expandedBot, setExpandedBot] = useState<string | null>(null)
  const [expandedServer, setExpandedServer] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [addingBot, setAddingBot] = useState(false)
  const [blockedInvite, setBlockedInvite] = useState<string | null>(null)
  const { run, notice } = useActionNotice()

  const loadData = useCallback(async () => {
    try {
      const [botsData, projectsData] = await Promise.all([
        listBots(userId),
        (teamsOn ? listProjects() : Promise.resolve<Project[]>([]))
      ])
      setBots(botsData)
      setProjects(projectsData)

      // If user has bots, load servers
      if (botsData.length > 0) {
        const serversData = await listServers()
        setServers(serversData)
      }
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load data')
    } finally {
      setLoading(false)
      refreshCounts()
    }
  }, [listBots, listServers, listProjects, teamsOn, userId, refreshCounts])

  useEffect(() => {
    loadData()
  }, [loadData])

  const loadChannels = async (serverId: string) => {
    if (Array.isArray(channelsByServer[serverId])) return
    setChannelsByServer(prev => Object.fromEntries(Object.entries(prev).filter(([id]) => id !== serverId)))
    try {
      const channels = await listChannels(serverId)
      setChannelsByServer(prev => ({ ...prev, [serverId]: channels }))
    } catch (e) {
      setChannelsByServer(prev => ({ ...prev, [serverId]: e instanceof Error ? e : new Error(String(e)) }))
    }
  }

  const handleAddBot = async (data: { name: string; token: string }) => {
    await createBot(data)
    setAddingBot(false)
    loadData()
  }

  const handleDeleteBot = (bot: DiscordBot) => run(async () => {
    await deleteBot(bot.id)
    loadData()
  })

  const handleToggleBotActive = (bot: DiscordBot) => run(async () => {
    await updateBot(bot.id, { is_active: !bot.is_active })
    loadData()
  })

  const handleRefreshMetadata = (botId: string) => run(async () => {
    await refreshBotMetadata(botId)
    // Reload servers after metadata refresh
    const serversData = await listServers()
    setServers(serversData)
    // Clear cached channels to force reload
    setChannelsByServer({})
  })

  const handleGetInviteUrl = (botId: string) => run(async () => {
    const { blockedUrl } = await openPopup(async () => (await getBotInviteUrl(botId)).invite_url, '', false)
    setBlockedInvite(blockedUrl)
  })

  const handleToggleServerCollect = (server: DiscordServer) => run(async () => {
    await updateServer(server.id, { collect_messages: !server.collect_messages })
    // Refresh servers
    const serversData = await listServers()
    setServers(serversData)
    // Refresh channels for this server (inheritance may have changed)
    if (channelsByServer[server.id]) {
      const channels = await listChannels(server.id)
      setChannelsByServer(prev => ({ ...prev, [server.id]: channels }))
    }
  })

  // The row shows a failed update next to the server.
  const handleUpdateServer = async (server: DiscordServer, updates: DiscordServerUpdate) => {
    await updateServer(server.id, updates)
    setServers(await listServers())
  }

  const handleUpdateChannel = (channel: DiscordChannel, updates: DiscordChannelUpdate) => run(async () => {
    const updatedChannel = await updateChannel(channel.id, updates)

    // Refresh channels for this server, or update single channel for DMs
    if (channel.server_id) {
      const channels = await listChannels(channel.server_id)
      setChannelsByServer(prev => ({ ...prev, [channel.server_id!]: channels }))
    } else {
      // For DM channels (no server_id), update the channel in place
      setChannelsByServer(prev => Object.fromEntries(Object.entries(prev).map(([serverId, channels]) =>
        [serverId, Array.isArray(channels) ? channels.map(ch => ch.id === channel.id ? updatedChannel : ch) : channels])))
    }
  })

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadData} />

  return (
    <Section title="Discord" actions={<Button variant="primary" onClick={() => setAddingBot(true)}>Add bot</Button>}>
      {notice}
      <PopupBlockedNotice url={blockedInvite} label="Open the Discord invite" onDismiss={() => setBlockedInvite(null)} />
      {bots.length === 0 ? (
        <EmptyState
          message="No Discord bots configured. Add a bot to start collecting messages."
          actionLabel="Add bot"
          onAction={() => setAddingBot(true)}
        />
      ) : (
        <List>
          {bots.map(bot => (
            <BotRow
              key={bot.id}
              bot={bot}
              servers={servers}
              channelsByServer={channelsByServer}
              projects={projects}
              expanded={expandedBot === bot.id}
              expandedServer={expandedServer}
              onToggleExpand={() => setExpandedBot(expandedBot === bot.id ? null : bot.id)}
              onRetryChannels={loadChannels}
              onExpandServer={(serverId) => {
                setExpandedServer(expandedServer === serverId ? null : serverId)
                if (expandedServer !== serverId) {
                  loadChannels(serverId)
                }
              }}
              onToggleActive={() => handleToggleBotActive(bot)}
              onRefresh={() => handleRefreshMetadata(bot.id)}
              onDelete={() => handleDeleteBot(bot)}
              onToggleServerCollect={handleToggleServerCollect}
              onUpdateServer={handleUpdateServer}
              onUpdateChannel={handleUpdateChannel}
              onGetInviteUrl={() => handleGetInviteUrl(bot.id)}
            />
          ))}
        </List>
      )}

      {addingBot && (
        <AddBotForm
          onAdd={handleAddBot}
          onCancel={() => setAddingBot(false)}
        />
      )}


    </Section>
  )
}

interface BotCardProps {
  bot: DiscordBot
  servers: DiscordServer[]
  channelsByServer: Record<string, DiscordChannel[] | Error>
  projects: Project[]
  expanded: boolean
  expandedServer: string | null
  onToggleExpand: () => void
  onExpandServer: (serverId: string) => void
  onRetryChannels: (serverId: string) => void
  onToggleActive: () => void
  onRefresh: () => Promise<unknown>
  onDelete: () => void
  onToggleServerCollect: (server: DiscordServer) => Promise<unknown>
  onUpdateServer: (server: DiscordServer, updates: DiscordServerUpdate) => Promise<unknown>
  onUpdateChannel: (channel: DiscordChannel, updates: DiscordChannelUpdate) => Promise<unknown>
  onGetInviteUrl: () => Promise<unknown>
}

const BotRow = ({
  bot,
  servers,
  channelsByServer,
  projects,
  expanded,
  expandedServer,
  onToggleExpand,
  onExpandServer,
  onRetryChannels,
  onToggleActive,
  onRefresh,
  onDelete,
  onToggleServerCollect,
  onUpdateServer,
  onUpdateChannel,
  onGetInviteUrl,
}: BotCardProps) => {
  return (
    <Row
      title={bot.name}
      muted={!bot.is_active}
      badges={<>
        <StatusBadge active={bot.is_active} />
        <Badge tone={bot.connected ? 'success' : 'danger'}>{bot.connected ? 'Connected' : 'Disconnected'}</Badge>
      </>}
      meta={[bot.updated_at && `Last sync: ${formatRelativeTime(bot.updated_at)}`]}
      actions={<>
        <SyncButton onSync={onRefresh} disabled={!bot.is_active} label="Refresh metadata" />
        <PauseButton active={bot.is_active} onClick={onToggleActive} />
        <Button size="sm" onClick={onGetInviteUrl}>Add to server</Button>
        <ConfirmButton confirm={`Remove "${bot.name}"? This removes the bot from Memory.`} onConfirm={onDelete}>Remove</ConfirmButton>
      </>}
    >
      <SubList
        title={<ExpandToggle expanded={expanded} onClick={onToggleExpand}>Servers ({servers.length})</ExpandToggle>}
        empty={expanded && 'No servers found. Click "Refresh metadata" to sync.'}
      >
        {expanded && servers.map(server => (
            <ServerRow
              key={server.id}
              server={server}
              channels={channelsByServer[server.id]}
              onRetryChannels={() => onRetryChannels(server.id)}
              projects={projects}
              expanded={expandedServer === server.id}
              onToggleExpand={() => onExpandServer(server.id)}
              onToggleCollect={() => onToggleServerCollect(server)}
              onUpdate={(updates) => onUpdateServer(server, updates)}
              onUpdateChannel={onUpdateChannel}
            />
          ))}
      </SubList>
    </Row>
  )
}

interface ServerCardProps {
  server: DiscordServer
  channels: DiscordChannel[] | Error | undefined
  onRetryChannels: () => void
  projects: Project[]
  expanded: boolean
  onToggleExpand: () => void
  onToggleCollect: () => Promise<unknown>
  onUpdate: (updates: DiscordServerUpdate) => Promise<unknown>
  onUpdateChannel: (channel: DiscordChannel, updates: DiscordChannelUpdate) => Promise<unknown>
}

const ServerRow = ({
  server,
  channels,
  onRetryChannels,
  projects,
  expanded,
  onToggleExpand,
  onToggleCollect,
  onUpdate,
  onUpdateChannel,
}: ServerCardProps) => {
  const [toggling, handleToggle] = useBusy(onToggleCollect)
  const update = useSubmit()

  return (
    <Row
      title={server.name}
      badges={<Badge tone={server.collect_messages ? 'success' : 'neutral'}>{server.collect_messages ? 'Collecting' : 'Paused'}</Badge>}
      meta={[server.member_count ? `${server.member_count} members` : null, `${server.channel_count} channels`]}
      actions={<>
        <AccessSelects projectId={server.project_id} sensitivity={server.sensitivity} projects={projects} disabled={update.busy}
          onChange={changes => update.submit(() => onUpdate(changes))} />
        <LLMKeyField compact value={server.llm_key} onChange={llm_key => update.submit(() => onUpdate({ llm_key }))} />
        <PauseButton active={server.collect_messages} onClick={handleToggle} disabled={toggling} />
      </>}
    >
      {update.error && <RowError live>{update.error}</RowError>}
      <SubList
        title={<ExpandToggle expanded={expanded} onClick={onToggleExpand}>Channels</ExpandToggle>}
        {...(expanded ? lazySubList(channels, 'channels', onRetryChannels) : {})}
      >
        {expanded && Array.isArray(channels) && channels.map(channel => (
            <ChannelRow
              key={channel.id}
              channel={channel}
              projects={projects}
              serverCollecting={server.collect_messages}
              onUpdate={(updates) => onUpdateChannel(channel, updates)}
            />
          ))}
      </SubList>
    </Row>
  )
}

interface ChannelRowProps {
  channel: DiscordChannel
  projects: Project[]
  serverCollecting: boolean
  onUpdate: (updates: DiscordChannelUpdate) => Promise<unknown>
}

const ChannelRow = ({ channel, projects, serverCollecting, onUpdate }: ChannelRowProps) => {
  const [updating, update] = useBusy(onUpdate)

  // Channel type icons
  const typeIcon = {
    text: '#',
    voice: '🔊',
    dm: '💬',
    group_dm: '👥',
    thread: '🧵',
  }[channel.channel_type] || '#'

  return (
    <Row
      className="py-2"
      title={<><span aria-hidden="true" className="text-slate-500 mr-1">{typeIcon}</span><span>{channel.name}</span></>}
      actions={<>
        <AccessSelects projectId={channel.project_id} sensitivity={channel.sensitivity} projects={projects} disabled={updating}
          onChange={update} />
        <CollectSelect value={channel.collect_messages} inherited={serverCollecting} disabled={updating}
          onChange={collect_messages => update({ collect_messages })} />
      </>}
    />
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
    <Modal title="Add Discord bot" onClose={onCancel}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}

        <Field label="Bot name" hint="A friendly name for this bot">
          <Input
            type="text"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="e.g., My Bot"
            required
          />
        </Field>

        <Field label="Bot token" hint="Get this from the Discord Developer Portal under Bot → Token">
          <Input
            type="password"
            value={token}
            onChange={e => setToken(e.target.value)}
            placeholder="Paste your Discord bot token"
            required
          />
        </Field>

        <FormActions>
          <Button
            type="button"
            onClick={onCancel}
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button variant="primary"
            type="submit"
            disabled={submitting}
          >
            {submitting ? 'Adding...' : 'Add bot'}
          </Button>
        </FormActions>
      </form>
    </Modal>
  )
}

export default DiscordPanel

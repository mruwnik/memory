import { useState, useEffect, useCallback } from 'react'
import { useDiscord, DiscordBot, DiscordBotCreate, DiscordBotUpdate, DiscordRealtime, DiscordServer, DiscordChannel, DiscordChannelUpdate, DiscordServerUpdate } from '@/hooks/useDiscord'
import { useModule } from '@/hooks/useModule'
import { useProjects, Project } from '@/hooks/useProjects'
import { AccessSelects, CollectSelect, PauseButton, PopupBlockedNotice, PUSH_CAUSES, PushBadge, StatusBadge, SyncButton, formatRelativeTime, lazySubList, openPopup, useBusy } from '../shared'
import { LLMKeyField } from '../LLMKeyField'
import { Badge, Button, ConfirmButton, Disclosure, EmptyState, ErrorState, ExpandToggle, Field, Fieldset, FormActions, Input, List, LoadingState, Modal, Notice, Row, RowError, Section, SubList, ToggleGroup, useActionNotice, useSubmit } from '@/components/shared/ui'
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
    listBotDms,
    updateChannel,
  } = useDiscord()
  const { listProjects } = useProjects()
  const teamsOn = useModule('teams')

  const [bots, setBots] = useState<DiscordBot[]>([])
  const [servers, setServers] = useState<DiscordServer[]>([])
  const [channelsByServer, setChannelsByServer] = useState<Record<string, DiscordChannel[] | Error>>({})
  const [dmsByBot, setDmsByBot] = useState<Record<string, DiscordChannel[] | Error>>({})
  const [expandedDms, setExpandedDms] = useState<string | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [expandedBot, setExpandedBot] = useState<string | null>(null)
  const [expandedServer, setExpandedServer] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [addingBot, setAddingBot] = useState(false)
  const [editingBot, setEditingBot] = useState<DiscordBot | null>(null)
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

  const loadDms = async (botId: string) => {
    setDmsByBot(prev => Object.fromEntries(Object.entries(prev).filter(([id]) => id !== botId)))
    try {
      const dms = await listBotDms(botId)
      setDmsByBot(prev => ({ ...prev, [botId]: dms }))
    } catch (e) {
      setDmsByBot(prev => ({ ...prev, [botId]: e instanceof Error ? e : new Error(String(e)) }))
    }
  }

  const handleAddBot = async (data: DiscordBotCreate) => {
    const created = await createBot(data)
    loadData()
    return created
  }

  const handleEditBot = async (bot: DiscordBot, updates: DiscordBotUpdate) => {
    await updateBot(bot.id, updates)
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

    // A server's channels are refetched, as inheritance may have changed; a DM inherits nothing.
    if (channel.server_id) {
      const channels = await listChannels(channel.server_id)
      setChannelsByServer(prev => ({ ...prev, [channel.server_id!]: channels }))
    } else {
      setDmsByBot(prev => Object.fromEntries(Object.entries(prev).map(([botId, dms]) =>
        [botId, Array.isArray(dms) ? dms.map(ch => ch.id === channel.id ? updatedChannel : ch) : dms])))
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
        <List separate>
          {bots.map(bot => (
            <BotRow
              key={bot.id}
              bot={bot}
              servers={servers.filter(server => server.bot_ids.includes(bot.id))}
              channelsByServer={channelsByServer}
              projects={projects}
              expanded={expandedBot === bot.id}
              expandedServer={expandedServer}
              onToggleExpand={() => setExpandedBot(expandedBot === bot.id ? null : bot.id)}
              onRetryChannels={loadChannels}
              dms={dmsByBot[bot.id]}
              dmsExpanded={expandedDms === bot.id}
              onToggleDms={() => {
                setExpandedDms(expandedDms === bot.id ? null : bot.id)
                if (expandedDms !== bot.id) loadDms(bot.id)
              }}
              onRetryDms={() => loadDms(bot.id)}
              onExpandServer={(serverId) => {
                setExpandedServer(expandedServer === serverId ? null : serverId)
                if (expandedServer !== serverId) {
                  loadChannels(serverId)
                }
              }}
              onEdit={() => setEditingBot(bot)}
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
      {editingBot && (
        <EditBotForm
          bot={editingBot}
          onSave={updates => handleEditBot(editingBot, updates)}
          onCancel={() => setEditingBot(null)}
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
  dms: DiscordChannel[] | Error | undefined
  dmsExpanded: boolean
  onToggleDms: () => void
  onRetryDms: () => void
  onEdit: () => void
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
  dms,
  dmsExpanded,
  onToggleDms,
  onRetryDms,
  onEdit,
  onToggleActive,
  onRefresh,
  onDelete,
  onToggleServerCollect,
  onUpdateServer,
  onUpdateChannel,
  onGetInviteUrl,
}: BotCardProps) => {
  const webhook = bot.realtime === 'webhook'
  return (
    <Row
      title={bot.name}
      muted={!bot.is_active}
      badges={<>
        <StatusBadge active={bot.is_active} />
        {webhook
          ? bot.is_active && <PushBadge setup="ok" causes={PUSH_CAUSES.discord} detail="Live messages arrive as webhook events."
              deliveries={{ push_missing_since: null, push_last_miss_at: null, push_stale: bot.push_stale, last_event_at: bot.last_event_at }} />
          : <Badge tone="info" title="Live messages arrive over the WebSocket gateway">Gateway</Badge>}
        {!webhook && bot.is_active && <Badge tone={bot.connected ? 'success' : 'danger'}>{bot.connected ? 'Connected' : 'Disconnected'}</Badge>}
      </>}
      meta={[
        webhook ? `Last event: ${formatRelativeTime(bot.last_event_at)}` : null,
        `Last sync: ${formatRelativeTime(bot.last_sync_at)}`,
      ]}
      actions={<>
        <Button size="sm" onClick={onEdit}>Edit</Button>
        <PauseButton active={bot.is_active} onClick={onToggleActive} />
        <ConfirmButton confirm={`Remove "${bot.name}"? This removes the bot from Memory.`} onConfirm={onDelete}>Remove</ConfirmButton>
      </>}
    >
      {!bot.has_token && <RowError>Add the bot's token in Edit to resume it.</RowError>}
      {webhook && <EventsUrl url={bot.events_url} />}
      <SubList
        indent
        title={<ExpandToggle expanded={expanded} onClick={onToggleExpand}>Servers ({servers.length})</ExpandToggle>}
        actions={<>
          {!webhook && <SyncButton onSync={onRefresh} disabled={!bot.is_active} label="Refresh metadata" />}
          {bot.api_base_url === DISCORD_API_BASE_URL && <Button size="sm" onClick={onGetInviteUrl}>Add to server</Button>}
        </>}
        empty={expanded && (webhook
          ? 'No servers yet. They appear with the first event or after the daily sync.'
          : 'No servers found. Click "Refresh metadata" to sync.')}
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
      <SubList
        indent
        title={<ExpandToggle expanded={dmsExpanded} onClick={onToggleDms}>Direct messages</ExpandToggle>}
        {...(dmsExpanded ? lazySubList(dms, 'DMs', onRetryDms) : {})}
      >
        {dmsExpanded && Array.isArray(dms) && dms.map(dm => (
          <ChannelRow key={dm.id} channel={dm} projects={projects} serverCollecting onUpdate={updates => onUpdateChannel(dm, updates)} />
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
        indent
        title={<ExpandToggle expanded={expanded} onClick={onToggleExpand}>Channels ({server.channel_count})</ExpandToggle>}
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

const EventsUrl = ({ url }: { url: string }) => (
  <Field label="Events URL" hint="Discord delivers this bot's messages here; paste it as the webhook events URL">
    <Input readOnly value={url} onFocus={e => e.target.select()} />
  </Field>
)

const REALTIME_OPTIONS: { value: DiscordRealtime; label: string }[] = [
  { value: 'gateway', label: 'WebSocket gateway' },
  { value: 'webhook', label: 'Webhook' },
]

interface RealtimeFieldsProps {
  realtime: DiscordRealtime
  onRealtime: (realtime: DiscordRealtime) => void
  publicKey: string
  onPublicKey: (publicKey: string) => void
  // Unknown until the bot is added
  eventsUrl?: string
}

const RealtimeFields = ({ realtime, onRealtime, publicKey, onPublicKey, eventsUrl }: RealtimeFieldsProps) => (
  <>
    <Fieldset legend="Real-time delivery" hint="How new messages reach Memory. Sending and the daily catch-up use the token either way.">
      <ToggleGroup label="Real-time delivery" options={REALTIME_OPTIONS} value={realtime} onChange={onRealtime} />
    </Fieldset>
    {realtime === 'webhook' && <>
      <Field label="Public key" hint="The key that signs the webhook events, as hex">
        <Input type="text" value={publicKey} onChange={e => onPublicKey(e.target.value)} required />
      </Field>
      {eventsUrl
        ? <EventsUrl url={eventsUrl} />
        : <Notice tone="info">The events URL to paste appears once the bot is added.</Notice>}
    </>}
  </>
)

const DISCORD_API_BASE_URL = 'https://discord.com/api/v10'

// A bot on another API server is set up there, not in Discord's Developer Portal.
const tokenSource = (apiBaseUrl: string) =>
  apiBaseUrl.trim() && apiBaseUrl.trim() !== DISCORD_API_BASE_URL
    ? `From the server at ${apiBaseUrl.trim().replace(/^https?:\/\/([^/]*).*$/, '$1')}`
    : null

const ApiBaseUrlField = ({ value, onChange }: { value: string; onChange: (value: string) => void }) => (
  <Disclosure summary="Advanced">
    <Field label="API base URL" hint={`Where the bot's REST API and gateway live; leave empty for ${DISCORD_API_BASE_URL}`}>
      <Input type="url" value={value} onChange={e => onChange(e.target.value)} placeholder={DISCORD_API_BASE_URL} />
    </Field>
  </Disclosure>
)

interface AddBotFormProps {
  onAdd: (data: DiscordBotCreate) => Promise<DiscordBot>
  onCancel: () => void
}

const AddBotForm = ({ onAdd, onCancel }: AddBotFormProps) => {
  const [realtime, setRealtime] = useState<DiscordRealtime>('gateway')
  const [name, setName] = useState('')
  const [applicationId, setApplicationId] = useState('')
  const [publicKey, setPublicKey] = useState('')
  const [token, setToken] = useState('')
  const [apiBaseUrl, setApiBaseUrl] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState<DiscordBot | null>(null)
  const webhook = realtime === 'webhook'

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const required = webhook ? [name, token, publicKey] : [name, token]
    if (required.some(value => !value.trim())) {
      setError('Please fill in all fields')
      return
    }

    setSubmitting(true)
    setError(null)

    const optional = (value: string) => value.trim() || undefined
    try {
      const bot = await onAdd({
        name: name.trim(),
        application_id: optional(applicationId),
        token: token.trim(),
        realtime,
        public_key: webhook ? publicKey.trim() : undefined,
        api_base_url: optional(apiBaseUrl),
      })
      if (bot.realtime !== 'webhook') {
        onCancel()
        return
      }
      setCreated(bot)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to add bot')
    } finally {
      setSubmitting(false)
    }
  }

  if (created) {
    return (
      <Modal title="Add Discord bot" onClose={onCancel}>
        <div className="space-y-4">
          <Notice tone="success">{created.name} is added. Its messages arrive once the sender delivers to this URL.</Notice>
          <EventsUrl url={created.events_url} />
          <FormActions><Button variant="primary" onClick={onCancel}>Done</Button></FormActions>
        </div>
      </Modal>
    )
  }

  return (
    <Modal title="Add Discord bot" onClose={onCancel}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}

        <Field label="Bot name" hint="A friendly name for this bot">
          <Input type="text" value={name} onChange={e => setName(e.target.value)} placeholder="e.g., My Bot" required />
        </Field>
        <Field label="Application ID (optional)" hint="Read from the token when left empty">
          <Input type="text" inputMode="numeric" value={applicationId} onChange={e => setApplicationId(e.target.value)} />
        </Field>
        <Field label="Bot token" hint={tokenSource(apiBaseUrl) ?? 'From the Discord Developer Portal under Bot → Token. On that page, also turn on the Message Content and Server Members intents'}>
          <Input type="password" value={token} onChange={e => setToken(e.target.value)} placeholder="Paste your Discord bot token" required />
        </Field>

        <RealtimeFields realtime={realtime} onRealtime={setRealtime} publicKey={publicKey} onPublicKey={setPublicKey} />
        <ApiBaseUrlField value={apiBaseUrl} onChange={setApiBaseUrl} />

        <FormActions>
          <Button type="button" onClick={onCancel} disabled={submitting}>Cancel</Button>
          <Button variant="primary" type="submit" disabled={submitting}>
            {submitting ? 'Adding...' : 'Add bot'}
          </Button>
        </FormActions>
      </form>
    </Modal>
  )
}

interface EditBotFormProps {
  bot: DiscordBot
  onSave: (updates: DiscordBotUpdate) => Promise<unknown>
  onCancel: () => void
}

const EditBotForm = ({ bot, onSave, onCancel }: EditBotFormProps) => {
  const [name, setName] = useState(bot.name)
  const [token, setToken] = useState('')
  const [realtime, setRealtime] = useState<DiscordRealtime>(bot.realtime)
  const [publicKey, setPublicKey] = useState(bot.public_key ?? '')
  const [apiBaseUrl, setApiBaseUrl] = useState(bot.api_base_url)
  const save = useSubmit()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const changed = (value: string, current: string | null) => value.trim() && value.trim() !== current ? value.trim() : undefined
    const updates: DiscordBotUpdate = {
      name: changed(name, bot.name),
      token: changed(token, null),
      realtime: realtime !== bot.realtime ? realtime : undefined,
      public_key: realtime === 'webhook' ? changed(publicKey, bot.public_key) : undefined,
      api_base_url: changed(apiBaseUrl, bot.api_base_url),
    }
    const sent = Object.fromEntries(Object.entries(updates).filter(([, value]) => value !== undefined))
    if (await save.submit(() => onSave(sent))) onCancel()
  }

  return (
    <Modal title="Edit Discord bot" onClose={onCancel}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {save.error && <Notice tone="error">{save.error}</Notice>}

        <Field label="Bot name">
          <Input type="text" value={name} onChange={e => setName(e.target.value)} required />
        </Field>
        <Field label={bot.has_token ? 'Bot token (leave empty to keep)' : 'Bot token'} hint={tokenSource(apiBaseUrl) ?? 'From the Discord Developer Portal under Bot → Token'}>
          <Input type="password" value={token} onChange={e => setToken(e.target.value)} placeholder="Paste a new token" required={!bot.has_token} />
        </Field>

        <RealtimeFields realtime={realtime} onRealtime={setRealtime} publicKey={publicKey} onPublicKey={setPublicKey} eventsUrl={bot.events_url} />
        <ApiBaseUrlField value={apiBaseUrl} onChange={setApiBaseUrl} />

        <FormActions>
          <Button type="button" onClick={onCancel} disabled={save.busy}>Cancel</Button>
          <Button variant="primary" type="submit" disabled={save.busy}>{save.busy ? 'Saving...' : 'Save'}</Button>
        </FormActions>
      </form>
    </Modal>
  )
}

export default DiscordPanel

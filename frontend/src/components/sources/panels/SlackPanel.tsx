import { useState, useEffect, useCallback } from 'react'
import { useSlack, SlackWorkspace, SlackWorkspaceApp, SlackChannel, SlackChannelUpdate, SlackWorkspaceUpdate } from '@/hooks/useSlack'
import { useSlackWizard, SlackAppResponse } from '@/hooks/useSlackWizard'
import { useProjects, Project } from '@/hooks/useProjects'
import { AccessSelects, CollectSelect, PauseButton, SyncButton, SyncFailure, formatRelativeTime, lazySubList, useBusy } from '../shared'
import { Badge, Button, ConfirmButton, EmptyState, ErrorState, ExpandToggle, List, LoadingState, Row, RowError, Section, SubList, Tone, cx, useActionNotice } from '@/components/shared/ui'
import { useSourcesContext } from '../Sources'
import { SlackAppWizard } from './SlackAppWizard'

export const SlackPanel = () => {
  const { userId } = useSourcesContext()
  const {
    listWorkspaces,
    updateWorkspace,
    deleteWorkspace,
    triggerSync,
    listChannels,
    updateChannel,
  } = useSlack()
  const { listProjects } = useProjects()
  const { getApp, listApps, deleteApp } = useSlackWizard()

  const [workspaces, setWorkspaces] = useState<SlackWorkspace[]>([])
  const [apps, setApps] = useState<SlackAppResponse[]>([])
  const [channelsByWorkspace, setChannelsByWorkspace] = useState<Record<string, SlackChannel[] | Error>>({})
  const [projects, setProjects] = useState<Project[]>([])
  const [expandedWorkspace, setExpandedWorkspace] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [wizardOpen, setWizardOpen] = useState(false)
  const [resume, setResume] = useState<{ app: SlackAppResponse; connected: boolean } | null>(null)
  const { run, notice } = useActionNotice()

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [workspacesData, projectsData, appsData] = await Promise.all([
        listWorkspaces(userId),
        listProjects(),
        listApps(),
      ])
      setWorkspaces(workspacesData)
      setProjects(projectsData)
      setApps(appsData)
      setLoaded(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load workspaces')
    } finally {
      setLoading(false)
    }
  }, [listWorkspaces, listProjects, listApps, userId])

  useEffect(() => {
    loadData()
  }, [loadData])

  // Listen for OAuth completion via BroadcastChannel.
  // The events endpoint scopes the channel name to slack-oauth-{user_id}
  // (§4 S12) so the oauth-complete event doesn't leak across tenants.
  useEffect(() => {
    if (userId === null || userId === undefined) return
    const channel = new BroadcastChannel(`slack-oauth-${userId}`)
    channel.onmessage = (event) => {
      if (event.data?.type === 'oauth-complete') {
        loadData()
      }
    }
    return () => channel.close()
  }, [loadData, userId])

  const loadChannels = async (workspaceId: string) => {
    if (Array.isArray(channelsByWorkspace[workspaceId])) return
    setChannelsByWorkspace(prev => Object.fromEntries(Object.entries(prev).filter(([id]) => id !== workspaceId)))
    try {
      const channels = await listChannels(workspaceId)
      setChannelsByWorkspace(prev => ({ ...prev, [workspaceId]: channels }))
    } catch (e) {
      setChannelsByWorkspace(prev => ({ ...prev, [workspaceId]: e instanceof Error ? e : new Error(String(e)) }))
    }
  }

  const handleConnect = () => {
    // Per-app wizard flow: register a SlackApp first, then OAuth into it.
    // No env-var single-app `/slack/authorize` endpoint anymore.
    setResume(null)
    setWizardOpen(true)
  }

  const handleResumeSetup = (app: SlackWorkspaceApp) => run(async () => {
    setResume({ app: await getApp(app.id), connected: true })
    setWizardOpen(true)
  })

  // Reload even on cancel: the wizard may already have created an app, which must show as unfinished.
  const closeWizard = () => {
    setWizardOpen(false)
    setResume(null)
    loadData()
  }

  const handleDeleteApp = (app: SlackAppResponse) => run(async () => {
    await deleteApp(app.id)
    loadData()
  })

  const handleDisconnect = (workspace: SlackWorkspace) => run(async () => {
    await deleteWorkspace(workspace.id)
    loadData()
  })

  const handleToggleCollect = (workspace: SlackWorkspace) => run(async () => {
    await updateWorkspace(workspace.id, { collect_messages: !workspace.collect_messages })
    loadData()
    // Refresh channels if loaded (inheritance may have changed)
    if (channelsByWorkspace[workspace.id]) {
      const channels = await listChannels(workspace.id)
      setChannelsByWorkspace(prev => ({ ...prev, [workspace.id]: channels }))
    }
  })

  const handleUpdateWorkspace = (workspace: SlackWorkspace, updates: SlackWorkspaceUpdate) => run(async () => {
    await updateWorkspace(workspace.id, updates)
    loadData()
  })

  const handleSync = (workspace: SlackWorkspace) => run(async () => {
    await triggerSync(workspace.id)
    // Reload to show updated sync status
    loadData()
  }, 'Sync queued')

  const handleUpdateChannel = (channel: SlackChannel, updates: SlackChannelUpdate) => run(async () => {
    await updateChannel(channel.id, updates)

    // Refresh channels
    const channels = await listChannels(channel.workspace_id)
    setChannelsByWorkspace(prev => ({ ...prev, [channel.workspace_id]: channels }))
  })

  // Apps the user started but never authorized: resuming one avoids creating a duplicate in Slack.
  const connectedAppIds = new Set(workspaces.flatMap(w => w.apps.map(a => a.id)))
  const unfinishedApps = apps.filter(app => app.is_owner && !EVENTS_LIVE.includes(app.setup_state) && !connectedAppIds.has(app.id))

  const body = loading && !loaded ? <LoadingState /> : error ? <ErrorState message={error} onRetry={loadData} /> : (
    <Section title="Slack" actions={<Button variant="primary" onClick={handleConnect}>Connect workspace</Button>}>
      {notice}

      {unfinishedApps.length > 0 && (
        <List>
          {unfinishedApps.map(app => (
            <Row key={app.id} title={app.name} badges={<Badge tone="warning">Setup incomplete</Badge>}
              meta={['No workspace connected yet']}
              actions={<>
                <Button size="sm" onClick={() => { setResume({ app, connected: false }); setWizardOpen(true) }}>Resume setup</Button>
                <ConfirmButton confirm={`Delete the unfinished Slack app "${app.name}"?`} onConfirm={() => handleDeleteApp(app)} />
              </>} />
          ))}
        </List>
      )}

      {workspaces.length > 0 ? (
        <List>
          {workspaces.map(workspace => (
            <WorkspaceRow
              key={workspace.id}
              workspace={workspace}
              channels={channelsByWorkspace[workspace.id]}
              onRetryChannels={() => loadChannels(workspace.id)}
              projects={projects}
              expanded={expandedWorkspace === workspace.id}
              onToggleExpand={() => {
                setExpandedWorkspace(expandedWorkspace === workspace.id ? null : workspace.id)
                if (expandedWorkspace !== workspace.id) {
                  loadChannels(workspace.id)
                }
              }}
              onToggleCollect={() => handleToggleCollect(workspace)}
              onUpdateWorkspace={(updates) => handleUpdateWorkspace(workspace, updates)}
              onSync={() => handleSync(workspace)}
              onResumeSetup={handleResumeSetup}
              onDisconnect={() => handleDisconnect(workspace)}
              onUpdateChannel={handleUpdateChannel}
            />
          ))}
        </List>
      ) : unfinishedApps.length === 0 && (
        <EmptyState
          message="No Slack workspaces connected. Connect a workspace to start collecting messages."
          actionLabel="Connect workspace"
          onAction={handleConnect}
        />
      )}
    </Section>
  )

  // The wizard sits outside the load states so a reload (e.g. on OAuth completion) doesn't remount it mid-setup.
  return <>
    {wizardOpen && (
      <SlackAppWizard
        key={resume?.app.id ?? 'new'}
        initialApp={resume?.app}
        initialAppConnected={resume?.connected}
        onComplete={closeWizard}
        onCancel={closeWizard}
      />
    )}
    {body}
  </>
}

// Apps whose push events are verified; the others are reached only by polling.
const EVENTS_LIVE = ['live', 'degraded']

type CollectionStatus = 'Not collecting' | 'Sync failing' | 'Stale' | 'Waiting for first sync' | 'Collecting'

const STATUS_TONES: Record<CollectionStatus, Tone> = {
  'Not collecting': 'danger',
  'Sync failing': 'danger',
  Stale: 'warning',
  'Waiting for first sync': 'neutral',
  Collecting: 'success',
}

const collectionStatus = (workspace: SlackWorkspace): CollectionStatus | null => {
  if (!workspace.collect_messages) return null
  if (workspace.poll_blocker) return 'Not collecting'
  if (workspace.sync_error) return 'Sync failing'
  if (workspace.sync_stale) return 'Stale'
  if (!workspace.last_sync_at) return 'Waiting for first sync'
  return 'Collecting'
}

interface WorkspaceCardProps {
  workspace: SlackWorkspace
  channels: SlackChannel[] | Error | undefined
  onRetryChannels: () => void
  projects: Project[]
  expanded: boolean
  onToggleExpand: () => void
  onToggleCollect: () => Promise<unknown>
  onUpdateWorkspace: (updates: SlackWorkspaceUpdate) => Promise<unknown>
  onSync: () => Promise<unknown>
  onResumeSetup: (app: SlackWorkspaceApp) => void
  onDisconnect: () => void
  onUpdateChannel: (channel: SlackChannel, updates: SlackChannelUpdate) => Promise<unknown>
}

const WorkspaceRow = ({
  workspace,
  channels,
  onRetryChannels,
  projects,
  expanded,
  onToggleExpand,
  onToggleCollect,
  onUpdateWorkspace,
  onSync,
  onResumeSetup,
  onDisconnect,
  onUpdateChannel,
}: WorkspaceCardProps) => {
  const [toggling, handleToggle] = useBusy(onToggleCollect)
  const [updating, update] = useBusy(onUpdateWorkspace)

  const status = collectionStatus(workspace)
  const eventlessApps = workspace.apps.filter(app => !EVENTS_LIVE.includes(app.setup_state))

  return (
    <Row
      title={workspace.name}
      badges={<>
        {status ? <Badge tone={STATUS_TONES[status]}>{status}</Badge> : <Badge>Paused</Badge>}
        {workspace.collect_messages && !workspace.poll_blocker && eventlessApps.map(app => (
          <Badge key={app.id} tone="info" title={`Push events aren't set up for ${app.name}, so new messages arrive on the periodic poll.${app.is_owner ? '' : " Ask the app's owner to finish its setup."}`}>
            Polling only
          </Badge>
        ))}
      </>}
      meta={[
        workspace.domain && `${workspace.domain}.slack.com`,
        `${workspace.channel_count} channels`,
        `${workspace.connected_users} connected users`,
        workspace.last_sync_at && `Last sync: ${formatRelativeTime(workspace.last_sync_at)}`,
      ]}
      actions={<>
        <AccessSelects projectId={workspace.project_id} sensitivity={workspace.sensitivity} projects={projects} disabled={updating}
          onChange={update} />
        <PauseButton active={workspace.collect_messages} onClick={handleToggle} disabled={toggling} />
        {eventlessApps.filter(app => app.is_owner).map(app => (
          <Button key={app.id} size="sm" onClick={() => onResumeSetup(app)} title={`Resume the setup wizard for ${app.name}`}>
            Finish event setup
          </Button>
        ))}
        <SyncButton onSync={onSync} label="Sync now" />
        <ConfirmButton confirm={`Disconnect "${workspace.name}"? This will stop collecting messages from this workspace.`} onConfirm={onDisconnect}>Disconnect</ConfirmButton>
      </>}
    >
      {workspace.collect_messages && workspace.poll_blocker && <RowError>Not collecting: {workspace.poll_blocker}</RowError>}
      <SyncFailure status={workspace} />
      <SubList
        title={<ExpandToggle expanded={expanded} onClick={onToggleExpand}>Channels ({workspace.channel_count})</ExpandToggle>}
        {...(expanded ? lazySubList(channels, 'channels', onRetryChannels) : {})}
      >
        {expanded && Array.isArray(channels) && channels.map(channel => (
          <ChannelRow
            key={channel.id}
            channel={channel}
            projects={projects}
            workspaceCollecting={workspace.collect_messages}
            onUpdate={(updates) => onUpdateChannel(channel, updates)}
          />
        ))}
      </SubList>
    </Row>
  )
}

interface ChannelRowProps {
  channel: SlackChannel
  projects: Project[]
  workspaceCollecting: boolean
  onUpdate: (updates: SlackChannelUpdate) => Promise<unknown>
}

const ChannelRow = ({ channel, projects, workspaceCollecting, onUpdate }: ChannelRowProps) => {
  const [updating, update] = useBusy(onUpdate)

  // Channel type icons
  const typeIcon = {
    channel: '#',
    dm: '💬',
    private_channel: '🔒',
    mpim: '👥',
  }[channel.channel_type] || '#'

  return (
    <Row
      className="py-2"
      muted={channel.is_archived}
      title={<span className={cx(channel.is_private && 'italic')}><span aria-hidden="true" className="text-slate-500 mr-1">{typeIcon}</span><span>{channel.name}</span></span>}
      badges={channel.is_archived && <Badge>Archived</Badge>}
      actions={<>
        <AccessSelects projectId={channel.project_id} sensitivity={channel.sensitivity} projects={projects} disabled={updating || channel.is_archived}
          onChange={update} />
        <CollectSelect value={channel.collect_messages} inherited={workspaceCollecting} disabled={updating || channel.is_archived}
          onChange={collect_messages => update({ collect_messages })} />
      </>}
    />
  )
}

export default SlackPanel

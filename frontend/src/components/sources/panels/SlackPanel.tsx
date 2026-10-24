import { useState, useEffect, useCallback } from 'react'
import { useSlack, SlackWorkspace, SlackWorkspaceApp, SlackChannel, SlackChannelUpdate, SlackWorkspaceUpdate } from '@/hooks/useSlack'
import { useSlackWizard, SlackAppResponse } from '@/hooks/useSlackWizard'
import { useProjects, Project } from '@/hooks/useProjects'
import { AccessSelects, SyncButton, formatRelativeTime } from '../shared'
import { Badge, Button, ConfirmButton, EmptyState, ErrorState, ExpandToggle, List, LoadingState, Notice, Row, RowError, Section, SubList, Tone, cx } from '@/components/shared/ui'
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
  const { getApp } = useSlackWizard()

  const [workspaces, setWorkspaces] = useState<SlackWorkspace[]>([])
  const [channelsByWorkspace, setChannelsByWorkspace] = useState<Record<string, SlackChannel[]>>({})
  const [projects, setProjects] = useState<Project[]>([])
  const [expandedWorkspace, setExpandedWorkspace] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [wizardOpen, setWizardOpen] = useState(false)
  const [resumeApp, setResumeApp] = useState<SlackAppResponse | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [workspacesData, projectsData] = await Promise.all([
        listWorkspaces(userId),
        listProjects()
      ])
      setWorkspaces(workspacesData)
      setProjects(projectsData)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load workspaces')
    } finally {
      setLoading(false)
    }
  }, [listWorkspaces, listProjects, userId])

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
    if (channelsByWorkspace[workspaceId]) return

    try {
      const channels = await listChannels(workspaceId)
      setChannelsByWorkspace(prev => ({ ...prev, [workspaceId]: channels }))
    } catch (e) {
      console.error('Failed to load channels:', e)
    }
  }

  const handleConnect = () => {
    // Per-app wizard flow: register a SlackApp first, then OAuth into it.
    // No env-var single-app `/slack/authorize` endpoint anymore.
    setResumeApp(null)
    setWizardOpen(true)
  }

  const handleResumeSetup = async (app: SlackWorkspaceApp) => {
    setActionError(null)
    try {
      setResumeApp(await getApp(app.id))
      setWizardOpen(true)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Failed to load the Slack app')
    }
  }

  const closeWizard = () => {
    setWizardOpen(false)
    setResumeApp(null)
  }

  const handleWizardComplete = () => {
    closeWizard()
    loadData()
  }

  const handleDisconnect = async (workspace: SlackWorkspace) => {
    await deleteWorkspace(workspace.id)
    loadData()
  }

  const handleToggleCollect = async (workspace: SlackWorkspace) => {
    await updateWorkspace(workspace.id, { collect_messages: !workspace.collect_messages })
    loadData()
    // Refresh channels if loaded (inheritance may have changed)
    if (channelsByWorkspace[workspace.id]) {
      const channels = await listChannels(workspace.id)
      setChannelsByWorkspace(prev => ({ ...prev, [workspace.id]: channels }))
    }
  }

  const handleUpdateWorkspace = async (workspace: SlackWorkspace, updates: SlackWorkspaceUpdate) => {
    await updateWorkspace(workspace.id, updates)
    loadData()
  }

  const handleSync = async (workspace: SlackWorkspace) => {
    setActionError(null)
    try {
      await triggerSync(workspace.id)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Failed to trigger sync')
      return
    }
    // Reload to show updated sync status
    loadData()
  }

  const handleUpdateChannel = async (channel: SlackChannel, updates: SlackChannelUpdate) => {
    await updateChannel(channel.id, updates)

    // Refresh channels
    const channels = await listChannels(channel.workspace_id)
    setChannelsByWorkspace(prev => ({ ...prev, [channel.workspace_id]: channels }))
  }

  const handleToggleChannelCollect = async (channel: SlackChannel) => {
    // Cycle through: inherit (null) -> on (true) -> off (false) -> inherit (null)
    let newValue: boolean | null
    if (channel.collect_messages === null) {
      newValue = true
    } else if (channel.collect_messages === true) {
      newValue = false
    } else {
      newValue = null
    }

    await handleUpdateChannel(channel, { collect_messages: newValue })
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadData} />

  return (
    <Section title="Slack" actions={<Button variant="primary" onClick={handleConnect}>Connect Workspace</Button>}>
      {wizardOpen && (
        <SlackAppWizard
          key={resumeApp?.id ?? 'new'}
          initialApp={resumeApp}
          onComplete={handleWizardComplete}
          onCancel={closeWizard}
        />
      )}
      {actionError && <Notice tone="error" onDismiss={() => setActionError(null)}>{actionError}</Notice>}

      {workspaces.length === 0 ? (
        <EmptyState
          message="No Slack workspaces connected. Connect a workspace to start collecting messages."
          actionLabel="Connect Workspace"
          onAction={handleConnect}
        />
      ) : (
        <List>
          {workspaces.map(workspace => (
            <WorkspaceRow
              key={workspace.id}
              workspace={workspace}
              channels={channelsByWorkspace[workspace.id] || []}
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
              onToggleChannelCollect={handleToggleChannelCollect}
              onUpdateChannel={handleUpdateChannel}
            />
          ))}
        </List>
      )}
    </Section>
  )
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
  channels: SlackChannel[]
  projects: Project[]
  expanded: boolean
  onToggleExpand: () => void
  onToggleCollect: () => Promise<void>
  onUpdateWorkspace: (updates: SlackWorkspaceUpdate) => Promise<void>
  onSync: () => Promise<void>
  onResumeSetup: (app: SlackWorkspaceApp) => void
  onDisconnect: () => void
  onToggleChannelCollect: (channel: SlackChannel) => Promise<void>
  onUpdateChannel: (channel: SlackChannel, updates: SlackChannelUpdate) => Promise<void>
}

const WorkspaceRow = ({
  workspace,
  channels,
  projects,
  expanded,
  onToggleExpand,
  onToggleCollect,
  onUpdateWorkspace,
  onSync,
  onResumeSetup,
  onDisconnect,
  onToggleChannelCollect,
  onUpdateChannel,
}: WorkspaceCardProps) => {
  const [toggling, setToggling] = useState(false)
  const [updating, setUpdating] = useState(false)

  const handleToggle = async () => {
    setToggling(true)
    try {
      await onToggleCollect()
    } finally {
      setToggling(false)
    }
  }

  const handleProjectChange = async (projectId: number | undefined) => {
    setUpdating(true)
    try {
      await onUpdateWorkspace({ project_id: projectId || null })
    } finally {
      setUpdating(false)
    }
  }

  const handleSensitivityChange = async (sensitivity: 'public' | 'basic' | 'internal' | 'confidential') => {
    setUpdating(true)
    try {
      await onUpdateWorkspace({ sensitivity })
    } finally {
      setUpdating(false)
    }
  }

  const status = collectionStatus(workspace)
  const eventlessApps = workspace.apps.filter(app => !EVENTS_LIVE.includes(app.setup_state))

  return (
    <Row
      title={workspace.name}
      badges={<>
        {status && <Badge tone={STATUS_TONES[status]}>{status}</Badge>}
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
          onProject={handleProjectChange} onSensitivity={handleSensitivityChange} />
        <Button size="sm" variant={workspace.collect_messages ? 'primary' : 'secondary'} aria-pressed={workspace.collect_messages}
          onClick={handleToggle} disabled={toggling}>
          {workspace.collect_messages ? 'Collection on' : 'Collection off'}
        </Button>
        {eventlessApps.filter(app => app.is_owner).map(app => (
          <Button key={app.id} size="sm" onClick={() => onResumeSetup(app)} title={`Resume the setup wizard for ${app.name}`}>
            Finish event setup
          </Button>
        ))}
        <SyncButton onSync={onSync} label="Sync Now" />
        <ConfirmButton confirm={`Disconnect "${workspace.name}"? This will stop collecting messages from this workspace.`} onConfirm={onDisconnect}>Disconnect</ConfirmButton>
      </>}
    >
      {workspace.collect_messages && workspace.poll_blocker && <RowError>Not collecting: {workspace.poll_blocker}</RowError>}
      {workspace.sync_error && <RowError>Error: {workspace.sync_error}</RowError>}
      <SubList
        title={<ExpandToggle expanded={expanded} onClick={onToggleExpand}>Channels ({workspace.channel_count})</ExpandToggle>}
        empty={expanded && 'Loading channels...'}
      >
        {expanded && channels.map(channel => (
          <ChannelRow
            key={channel.id}
            channel={channel}
            projects={projects}
            workspaceCollecting={workspace.collect_messages}
            onToggle={() => onToggleChannelCollect(channel)}
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
  onToggle: () => Promise<void>
  onUpdate: (updates: SlackChannelUpdate) => Promise<void>
}

const ChannelRow = ({ channel, projects, workspaceCollecting, onToggle, onUpdate }: ChannelRowProps) => {
  const [toggling, setToggling] = useState(false)

  const handleToggle = async () => {
    setToggling(true)
    try {
      await onToggle()
    } finally {
      setToggling(false)
    }
  }

  const isInheriting = channel.collect_messages === null
  const effectivelyCollecting = channel.effective_collect

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
        <AccessSelects projectId={channel.project_id} sensitivity={channel.sensitivity} projects={projects} disabled={channel.is_archived}
          onProject={id => onUpdate({ project_id: id })} onSensitivity={sensitivity => onUpdate({ sensitivity })} />
        <Button size="sm" onClick={handleToggle} disabled={toggling || channel.is_archived}
          title={isInheriting
            ? `Inheriting from workspace (${workspaceCollecting ? 'collecting' : 'not collecting'})`
            : channel.collect_messages ? 'Explicitly collecting' : 'Explicitly not collecting'}>
          {isInheriting
            ? `Inherit (${effectivelyCollecting ? 'yes' : 'no'})`
            : channel.collect_messages ? 'Collecting' : 'Skipping'}
        </Button>
      </>}
    />
  )
}

export default SlackPanel

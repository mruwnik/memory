import { useState, useEffect, useCallback } from 'react'
import { useSlack, SlackWorkspace, SlackChannel } from '@/hooks/useSlack'
import {
  EmptyState,
  LoadingState,
  ErrorState,
  SyncStatus,
  SyncButton,
  ConfirmDialog,
} from '../shared'
import { styles, cx } from '../styles'

export const SlackPanel = () => {
  const {
    getAuthorizeUrl,
    listWorkspaces,
    updateWorkspace,
    deleteWorkspace,
    triggerSync,
    listChannels,
    updateChannel,
  } = useSlack()

  const [workspaces, setWorkspaces] = useState<SlackWorkspace[]>([])
  const [channelsByWorkspace, setChannelsByWorkspace] = useState<Record<string, SlackChannel[]>>({})
  const [expandedWorkspace, setExpandedWorkspace] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [disconnecting, setDisconnecting] = useState<SlackWorkspace | null>(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listWorkspaces()
      setWorkspaces(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load workspaces')
    } finally {
      setLoading(false)
    }
  }, [listWorkspaces])

  useEffect(() => {
    loadData()
  }, [loadData])

  const loadChannels = async (workspaceId: string) => {
    if (channelsByWorkspace[workspaceId]) return

    try {
      const channels = await listChannels(workspaceId)
      setChannelsByWorkspace(prev => ({ ...prev, [workspaceId]: channels }))
    } catch (e) {
      console.error('Failed to load channels:', e)
    }
  }

  const handleConnect = async () => {
    try {
      const { authorization_url } = await getAuthorizeUrl()
      // Open OAuth flow in a popup or redirect
      window.open(authorization_url, '_blank', 'width=600,height=700')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to start OAuth flow')
    }
  }

  const handleDisconnect = async (workspace: SlackWorkspace) => {
    await deleteWorkspace(workspace.id)
    setDisconnecting(null)
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

  const handleSync = async (workspace: SlackWorkspace) => {
    await triggerSync(workspace.id)
    // Reload to show updated sync status
    loadData()
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

    await updateChannel(channel.id, { collect_messages: newValue })

    // Refresh channels
    const channels = await listChannels(channel.workspace_id)
    setChannelsByWorkspace(prev => ({ ...prev, [channel.workspace_id]: channels }))
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadData} />

  return (
    <div className={styles.panel}>
      <div className={styles.panelHeader}>
        <h3 className={styles.panelTitle}>Slack</h3>
        <button className={styles.btnAdd} onClick={handleConnect}>
          Connect Workspace
        </button>
      </div>

      {workspaces.length === 0 ? (
        <EmptyState
          message="No Slack workspaces connected. Connect a workspace to start collecting messages."
          actionLabel="Connect Workspace"
          onAction={handleConnect}
        />
      ) : (
        <div className={styles.sourceList}>
          {workspaces.map(workspace => (
            <WorkspaceCard
              key={workspace.id}
              workspace={workspace}
              channels={channelsByWorkspace[workspace.id] || []}
              expanded={expandedWorkspace === workspace.id}
              onToggleExpand={() => {
                setExpandedWorkspace(expandedWorkspace === workspace.id ? null : workspace.id)
                if (expandedWorkspace !== workspace.id) {
                  loadChannels(workspace.id)
                }
              }}
              onToggleCollect={() => handleToggleCollect(workspace)}
              onSync={() => handleSync(workspace)}
              onDisconnect={() => setDisconnecting(workspace)}
              onToggleChannelCollect={handleToggleChannelCollect}
            />
          ))}
        </div>
      )}

      {disconnecting && (
        <ConfirmDialog
          message={`Are you sure you want to disconnect "${disconnecting.name}"? This will stop collecting messages from this workspace.`}
          onConfirm={() => handleDisconnect(disconnecting)}
          onCancel={() => setDisconnecting(null)}
        />
      )}
    </div>
  )
}

interface WorkspaceCardProps {
  workspace: SlackWorkspace
  channels: SlackChannel[]
  expanded: boolean
  onToggleExpand: () => void
  onToggleCollect: () => Promise<void>
  onSync: () => Promise<void>
  onDisconnect: () => void
  onToggleChannelCollect: (channel: SlackChannel) => Promise<void>
}

const WorkspaceCard = ({
  workspace,
  channels,
  expanded,
  onToggleExpand,
  onToggleCollect,
  onSync,
  onDisconnect,
  onToggleChannelCollect,
}: WorkspaceCardProps) => {
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
    <div className="border border-slate-200 rounded-lg p-4">
      <div className={styles.cardHeader}>
        <div className={styles.cardInfo}>
          <h4 className={styles.cardTitle}>{workspace.name}</h4>
          <div className="flex items-center gap-2 text-sm text-slate-500 mt-1">
            {workspace.domain && (
              <>
                <span>{workspace.domain}.slack.com</span>
                <span className="text-slate-300">|</span>
              </>
            )}
            <span>{workspace.channel_count} channels</span>
            <span className="text-slate-300">|</span>
            <span>{workspace.user_count} users</span>
            {workspace.last_sync_at && (
              <>
                <span className="text-slate-300">|</span>
                <SyncStatus lastSyncAt={workspace.last_sync_at} />
              </>
            )}
          </div>
          {workspace.sync_error && (
            <div className="text-sm text-red-500 mt-1">
              Error: {workspace.sync_error}
            </div>
          )}
        </div>
        <button
          className={cx(
            'px-3 py-1 rounded text-sm',
            workspace.collect_messages
              ? 'bg-green-100 text-green-700 hover:bg-green-200'
              : 'bg-slate-100 text-slate-600 hover:bg-slate-200',
            toggling && 'opacity-50'
          )}
          onClick={handleToggle}
          disabled={toggling}
        >
          {workspace.collect_messages ? 'Collecting' : 'Not Collecting'}
        </button>
      </div>

      <div className="flex gap-2 mt-3">
        <SyncButton onSync={onSync} label="Sync Now" />
        <button className={styles.btnDelete} onClick={onDisconnect}>
          Disconnect
        </button>
      </div>

      {/* Channels section */}
      <div className="mt-4 pt-4 border-t border-slate-100">
        <button
          className="flex items-center gap-2 text-sm font-medium text-slate-700 hover:text-slate-900"
          onClick={onToggleExpand}
        >
          <span className={cx('transition-transform', expanded && 'rotate-90')}>
            ▶
          </span>
          Channels ({workspace.channel_count})
        </button>

        {expanded && channels.length === 0 && (
          <p className="text-sm text-slate-400 italic mt-2 ml-5">
            Loading channels...
          </p>
        )}

        {expanded && channels.length > 0 && (
          <div className="mt-2 ml-5 space-y-1">
            {channels.map(channel => (
              <ChannelRow
                key={channel.id}
                channel={channel}
                workspaceCollecting={workspace.collect_messages}
                onToggle={() => onToggleChannelCollect(channel)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

interface ChannelRowProps {
  channel: SlackChannel
  workspaceCollecting: boolean
  onToggle: () => Promise<void>
}

const ChannelRow = ({ channel, workspaceCollecting, onToggle }: ChannelRowProps) => {
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
    group_dm: '👥',
    mpim: '👥',
  }[channel.channel_type] || '#'

  return (
    <div className={cx(
      "flex items-center justify-between py-1 px-2 rounded hover:bg-slate-50",
      channel.is_archived && "opacity-50"
    )}>
      <div className="flex items-center gap-2 text-sm">
        <span className="text-slate-400">{typeIcon}</span>
        <span className={cx(
          "text-slate-700",
          channel.is_private && "italic"
        )}>
          {channel.name}
          {channel.is_archived && ' (archived)'}
        </span>
      </div>
      <button
        className={cx(
          'px-2 py-0.5 rounded text-xs flex items-center gap-1',
          toggling && 'opacity-50',
          isInheriting
            ? effectivelyCollecting
              ? 'bg-blue-50 text-blue-600 hover:bg-blue-100'
              : 'bg-slate-50 text-slate-500 hover:bg-slate-100'
            : channel.collect_messages
              ? 'bg-green-100 text-green-700 hover:bg-green-200'
              : 'bg-red-50 text-red-600 hover:bg-red-100'
        )}
        onClick={handleToggle}
        disabled={toggling || channel.is_archived}
        title={
          isInheriting
            ? `Inheriting from workspace (${workspaceCollecting ? 'collecting' : 'not collecting'})`
            : channel.collect_messages
              ? 'Explicitly collecting'
              : 'Explicitly not collecting'
        }
      >
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

export default SlackPanel

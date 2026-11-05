import { useState, useEffect, useCallback } from 'react'
import { useBeeper, BeeperAccount, BeeperAccountUpdate, BeeperChat, BeeperChatUpdate, BeeperNetwork, BeeperNetworkUpdate } from '@/hooks/useBeeper'
import { useModule } from '@/hooks/useModule'
import { useProjects, Project } from '@/hooks/useProjects'
import { AccessSelects, CollectSelect, PauseButton, StatusBadge, SyncButton, formatRelativeTime, lazySubList, useBusy } from '../shared'
import { LLMKeyField } from '../LLMKeyField'
import { Badge, Button, Checkbox, ConfirmButton, EmptyState, ErrorState, ExpandToggle, Field, FormActions, Hint, Input, List, LoadingState, Modal, Notice, Row, RowError, Section, SubList, Tone, useActionNotice, useSubmit } from '@/components/shared/ui'
import { useSourcesContext } from '../context'
import { BeeperSetup, CloudNote } from './BeeperSetup'

const NETWORK_STATUS_TONES: Record<string, Tone> = {
  connected: 'success',
  backfilling: 'info',
  removed: 'danger',
}

const RESUME_LABELS = { email: 'Sign in', code: 'Enter code', verify: 'Verify device', done: '' }

export const BeeperPanel = () => {
  const { userId, refreshCounts } = useSourcesContext()
  const { listAccounts, updateAccount, deleteAccount, signOut, listNetworks, refreshNetworks, updateNetwork, listChats, updateChat } = useBeeper()
  const { listProjects } = useProjects()
  const teamsOn = useModule('teams')

  const [accounts, setAccounts] = useState<BeeperAccount[]>([])
  const [networksByAccount, setNetworksByAccount] = useState<Record<number, BeeperNetwork[] | Error>>({})
  const [chatsByNetwork, setChatsByNetwork] = useState<Record<number, BeeperChat[] | Error>>({})
  const [projects, setProjects] = useState<Project[]>([])
  const [expandedNetwork, setExpandedNetwork] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // undefined: closed; null: adding a new account
  const [setup, setSetup] = useState<BeeperAccount | null | undefined>(undefined)
  const [editing, setEditing] = useState<BeeperAccount | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const { run, notice } = useActionNotice()

  const loadNetworks = useCallback(async (accountId: number) => {
    try {
      const networks = await listNetworks(accountId)
      setNetworksByAccount(prev => ({ ...prev, [accountId]: networks }))
    } catch (e) {
      setNetworksByAccount(prev => ({ ...prev, [accountId]: e instanceof Error ? e : new Error(String(e)) }))
    }
  }, [listNetworks])

  const loadData = useCallback(async () => {
    try {
      const [accountsData, projectsData] = await Promise.all([
        listAccounts(userId),
        teamsOn ? listProjects() : Promise.resolve<Project[]>([]),
      ])
      setAccounts(accountsData)
      setProjects(projectsData)
      setError(null)
      accountsData.forEach(account => loadNetworks(account.id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load Beeper accounts')
    } finally {
      setLoading(false)
      refreshCounts()
    }
  }, [listAccounts, listProjects, loadNetworks, teamsOn, userId, refreshCounts])

  useEffect(() => {
    loadData()
  }, [loadData])

  const loadChats = async (networkId: number) => {
    setChatsByNetwork(prev => Object.fromEntries(Object.entries(prev).filter(([id]) => Number(id) !== networkId)))
    try {
      const chats = await listChats(networkId)
      setChatsByNetwork(prev => ({ ...prev, [networkId]: chats }))
    } catch (e) {
      setChatsByNetwork(prev => ({ ...prev, [networkId]: e instanceof Error ? e : new Error(String(e)) }))
    }
  }

  const handleRefreshNetworks = (account: BeeperAccount) => run(async () => {
    const networks = await refreshNetworks(account.id)
    setNetworksByAccount(prev => ({ ...prev, [account.id]: networks }))
  })

  const handleSignOut = (account: BeeperAccount) => run(async () => {
    setNote((await signOut(account.id)).note)
    loadData()
  })

  const handleDelete = (account: BeeperAccount) => run(async () => {
    const result = await deleteAccount(account.id)
    setNote(result.signed_out ? result.note : `Deleted, but memory couldn't sign out of Beeper first. ${result.note}`)
    loadData()
  })

  const handleUpdateAccount = (account: BeeperAccount, updates: BeeperAccountUpdate) => run(async () => {
    await updateAccount(account.id, updates)
    loadData()
  })

  // The row shows a failed update next to the network.
  const handleUpdateNetwork = async (network: BeeperNetwork, updates: BeeperNetworkUpdate) => {
    const updated = await updateNetwork(network.id, updates)
    setNetworksByAccount(prev => {
      const networks = prev[network.account_id]
      return Array.isArray(networks) ? { ...prev, [network.account_id]: networks.map(n => n.id === updated.id ? updated : n) } : prev
    })
    // Chats that inherit the network's setting change with it
    if (updates.collect_messages !== undefined && chatsByNetwork[network.id]) loadChats(network.id)
  }

  const handleUpdateChat = (chat: BeeperChat, updates: BeeperChatUpdate) => run(async () => {
    const updated = await updateChat(chat.id, updates)
    setChatsByNetwork(prev => {
      const chats = prev[chat.network_id]
      return Array.isArray(chats) ? { ...prev, [chat.network_id]: chats.map(c => c.id === updated.id ? updated : c) } : prev
    })
  })

  const closeSetup = () => {
    setSetup(undefined)
    loadData()
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadData} />

  return (
    <Section title="Beeper" actions={<Button variant="primary" onClick={() => setSetup(null)}>Add Beeper account</Button>}>
      <CloudNote />
      {notice}
      {note && <Notice tone="warning" onDismiss={() => setNote(null)}>{note}</Notice>}
      {accounts.length === 0 ? (
        <EmptyState
          message="No Beeper accounts yet. Add one to collect the chats you've connected in Beeper."
          actionLabel="Add Beeper account"
          onAction={() => setSetup(null)}
        />
      ) : (
        <List separate>
          {accounts.map(account => (
            <AccountRow
              key={account.id}
              account={account}
              networks={networksByAccount[account.id]}
              chatsByNetwork={chatsByNetwork}
              projects={projects}
              expandedNetwork={expandedNetwork}
              onRetryNetworks={() => loadNetworks(account.id)}
              onExpandNetwork={networkId => {
                setExpandedNetwork(expandedNetwork === networkId ? null : networkId)
                if (expandedNetwork !== networkId) loadChats(networkId)
              }}
              onRetryChats={loadChats}
              onResume={() => setSetup(account)}
              onEdit={() => setEditing(account)}
              onToggleActive={() => handleUpdateAccount(account, { is_active: !account.is_active })}
              onRefresh={() => handleRefreshNetworks(account)}
              onSignOut={() => handleSignOut(account)}
              onDelete={() => handleDelete(account)}
              onUpdateNetwork={handleUpdateNetwork}
              onUpdateChat={handleUpdateChat}
            />
          ))}
        </List>
      )}

      {setup !== undefined && <BeeperSetup account={setup} onClose={closeSetup} />}
      {editing && (
        <EditAccountForm
          account={editing}
          onSave={async updates => { await updateAccount(editing.id, updates); loadData() }}
          onCancel={() => setEditing(null)}
        />
      )}
    </Section>
  )
}

interface AccountRowProps {
  account: BeeperAccount
  networks: BeeperNetwork[] | Error | undefined
  chatsByNetwork: Record<number, BeeperChat[] | Error>
  projects: Project[]
  expandedNetwork: number | null
  onRetryNetworks: () => void
  onExpandNetwork: (networkId: number) => void
  onRetryChats: (networkId: number) => void
  onResume: () => void
  onEdit: () => void
  onToggleActive: () => void
  onRefresh: () => Promise<unknown>
  onSignOut: () => void
  onDelete: () => void
  onUpdateNetwork: (network: BeeperNetwork, updates: BeeperNetworkUpdate) => Promise<unknown>
  onUpdateChat: (chat: BeeperChat, updates: BeeperChatUpdate) => Promise<unknown>
}

const AccountRow = ({
  account,
  networks,
  chatsByNetwork,
  projects,
  expandedNetwork,
  onRetryNetworks,
  onExpandNetwork,
  onRetryChats,
  onResume,
  onEdit,
  onToggleActive,
  onRefresh,
  onSignOut,
  onDelete,
  onUpdateNetwork,
  onUpdateChat,
}: AccountRowProps) => {
  const title = account.name || account.email || account.server_url
  const lazy = lazySubList(networks, 'networks', onRetryNetworks)
  return (
    <Row
      title={title}
      muted={!account.is_active}
      badges={<>
        <StatusBadge active={account.is_active} />
        {!account.signed_in
          ? <Badge tone="neutral">Signed out</Badge>
          : account.verified ? <Badge tone="success">Verified</Badge> : <Badge tone="warning">Signed in, not verified</Badge>}
        {account.signed_in && account.setup_state !== 'ready' && <Badge tone="warning" title="The Beeper Server's sign-in state">{account.setup_state}</Badge>}
        {!account.version_matches && <Badge tone="warning">Server version mismatch</Badge>}
        {account.connected
          ? <Badge tone="success" title="The collector's WebSocket to the Beeper Server is up">Connected</Badge>
          : account.is_active && account.verified && (
            <Badge tone="warning" title="The collector has no WebSocket to the Beeper Server, so new messages arrive only with the next reconcile">Not connected</Badge>
          )}
      </>}
      meta={[
        account.server_url,
        account.email,
        `Last event: ${formatRelativeTime(account.last_event_at)}`,
        `Last reconcile: ${formatRelativeTime(account.last_reconcile_at)}`,
      ]}
      actions={<>
        {account.step !== 'done' && <Button size="sm" variant="primary" onClick={onResume}>{RESUME_LABELS[account.step]}</Button>}
        <Button size="sm" onClick={onEdit}>Edit</Button>
        <PauseButton active={account.is_active} onClick={onToggleActive} />
        {account.signed_in && (
          <ConfirmButton variant="secondary" onConfirm={onSignOut}
            confirm={`Sign "${title}" out of Beeper? Memory stops collecting its chats. Signing in again needs the operator to wipe the Beeper Server's data directory first.`}>
            Sign out
          </ConfirmButton>
        )}
        <ConfirmButton onConfirm={onDelete}
          confirm={`Delete "${title}"? This signs it out of Beeper and deletes its networks, chats and collected messages from memory.`} />
      </>}
    >
      {account.signed_in && !account.verified && (
        <RowError>This device isn't verified, so memory can't read encrypted chats. Use “Verify device” to approve it again.</RowError>
      )}
      {!account.version_matches && (
        <Notice tone="warning">
          The Beeper Server runs {account.server_version}, not the version memory is pinned to. Ask the operator to update it.
        </Notice>
      )}
      <SubList
        indent
        title={`Networks (${Array.isArray(networks) ? networks.length : '…'})`}
        actions={<SyncButton onSync={onRefresh} disabled={!account.signed_in} label="Refresh networks" />}
        {...lazy}
        empty={Array.isArray(networks) ? (account.signed_in
          ? 'No networks yet. Connect one with Beeper Cloud in the Beeper app, then refresh networks.'
          : 'No networks. Sign in to list them.') : lazy.empty}
      >
        {Array.isArray(networks) && networks.map(network => (
          <NetworkRow
            key={network.id}
            network={network}
            chats={chatsByNetwork[network.id]}
            projects={projects}
            expanded={expandedNetwork === network.id}
            onToggleExpand={() => onExpandNetwork(network.id)}
            onRetryChats={() => onRetryChats(network.id)}
            onUpdate={updates => onUpdateNetwork(network, updates)}
            onUpdateChat={onUpdateChat}
          />
        ))}
      </SubList>
    </Row>
  )
}

interface NetworkRowProps {
  network: BeeperNetwork
  chats: BeeperChat[] | Error | undefined
  projects: Project[]
  expanded: boolean
  onToggleExpand: () => void
  onRetryChats: () => void
  onUpdate: (updates: BeeperNetworkUpdate) => Promise<unknown>
  onUpdateChat: (chat: BeeperChat, updates: BeeperChatUpdate) => Promise<unknown>
}

const NetworkRow = ({ network, chats, projects, expanded, onToggleExpand, onRetryChats, onUpdate, onUpdateChat }: NetworkRowProps) => {
  const update = useSubmit()
  const status = network.status?.toLowerCase()
  const removed = status === 'removed'
  return (
    <Row
      title={network.display_name || network.network}
      muted={removed}
      badges={<>
        <Badge tone="info">{network.network}</Badge>
        {status && (
          <Badge tone={NETWORK_STATUS_TONES[status] ?? 'neutral'} title={removed ? 'No longer connected in Beeper' : undefined}>
            {status}
          </Badge>
        )}
        <Badge tone={network.collect_messages ? 'success' : 'neutral'}>{network.collect_messages ? 'Collecting' : 'Paused'}</Badge>
      </>}
      actions={<>
        <AccessSelects projectId={network.project_id} sensitivity={network.sensitivity} projects={projects} disabled={update.busy}
          onChange={changes => update.submit(() => onUpdate(changes))} />
        <LLMKeyField compact value={network.llm_key} onChange={llm_key => update.submit(() => onUpdate({ llm_key }))} />
        <PauseButton active={network.collect_messages} disabled={update.busy}
          onClick={() => update.submit(() => onUpdate({ collect_messages: !network.collect_messages }))} />
      </>}
    >
      {update.error && <RowError live>{update.error}</RowError>}
      <SubList
        indent
        title={<ExpandToggle expanded={expanded} onClick={onToggleExpand}>Chats</ExpandToggle>}
        {...(expanded ? lazySubList(chats, 'chats', onRetryChats) : {})}
      >
        {expanded && Array.isArray(chats) && chats.map(chat => (
          <ChatRow key={chat.id} chat={chat} projects={projects} networkCollecting={network.collect_messages}
            networkSensitivity={network.sensitivity} onUpdate={updates => onUpdateChat(chat, updates)} />
        ))}
      </SubList>
    </Row>
  )
}

const ChatRow = ({ chat, projects, networkCollecting, networkSensitivity, onUpdate }: {
  chat: BeeperChat
  projects: Project[]
  networkCollecting: boolean
  networkSensitivity: string
  onUpdate: (updates: BeeperChatUpdate) => Promise<unknown>
}) => {
  const [updating, update] = useBusy(onUpdate)
  return (
    <Row
      className="py-2"
      title={chat.title || chat.chat_id}
      badges={<>
        {chat.chat_type && <Badge tone="neutral">{chat.chat_type}</Badge>}
        {chat.is_merged && <Badge tone="neutral" title="Its messages are collected from the chats merged into it">Merged</Badge>}
        {!!chat.message_expiry_seconds && <Badge tone="warning" title="Messages disappear after a while">Disappearing</Badge>}
        {chat.backfill_stopped && (
          <Badge tone="warning" title="The Beeper Server kept answering with no older history, so memory paused asking; it tries again on the next reconcile">History incomplete</Badge>
        )}
      </>}
      meta={[chat.last_activity_at && `Last activity: ${formatRelativeTime(chat.last_activity_at)}`]}
      actions={<>
        <AccessSelects projectId={chat.project_id} sensitivity={chat.sensitivity} inheritedSensitivity={networkSensitivity}
          projects={projects} disabled={updating} onChange={update} />
        {!chat.is_merged && (
          <CollectSelect value={chat.collect_messages} inherited={networkCollecting} disabled={updating}
            onChange={collect_messages => update({ collect_messages })} />
        )}
      </>}
    />
  )
}

const EditAccountForm = ({ account, onSave, onCancel }: {
  account: BeeperAccount
  onSave: (updates: BeeperAccountUpdate) => Promise<unknown>
  onCancel: () => void
}) => {
  const [name, setName] = useState(account.name ?? '')
  const [collect, setCollect] = useState(account.collect_by_default)
  const save = useSubmit()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (await save.submit(() => onSave({ name: name.trim(), collect_by_default: collect }))) onCancel()
  }

  return (
    <Modal title="Edit Beeper account" onClose={onCancel}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {save.error && <Notice tone="error">{save.error}</Notice>}
        <Field label="Name">
          <Input type="text" value={name} onChange={e => setName(e.target.value)} />
        </Field>
        <div>
          <Checkbox label="Collect new networks" checked={collect} onChange={e => setCollect(e.target.checked)} />
          <Hint>Networks connected in Beeper from now on start collecting. Existing networks keep their setting.</Hint>
        </div>
        <FormActions>
          <Button onClick={onCancel} disabled={save.busy}>Cancel</Button>
          <Button variant="primary" type="submit" disabled={save.busy}>{save.busy ? 'Saving...' : 'Save'}</Button>
        </FormActions>
      </form>
    </Modal>
  )
}

export default BeeperPanel

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithUser, screen, waitFor, within } from '@/test/utils'

const listWorkspaces = vi.fn()
const updateWorkspace = vi.fn()
const deleteWorkspace = vi.fn()
const triggerSync = vi.fn()
const listChannels = vi.fn()
const updateChannel = vi.fn()
const listProjects = vi.fn()

vi.mock('@/hooks/useSlack', () => ({
  useSlack: () => ({
    listWorkspaces,
    updateWorkspace,
    deleteWorkspace,
    triggerSync,
    listChannels,
    updateChannel,
  }),
}))

const getApp = vi.fn()
const listApps = vi.fn()
const deleteApp = vi.fn()

vi.mock('@/hooks/useSlackWizard', () => ({
  useSlackWizard: () => ({ getApp, listApps, deleteApp }),
}))

vi.mock('@/hooks/useProjects', () => ({
  useProjects: () => ({ listProjects }),
}))

vi.mock('../context', () => {
  const refreshCounts = () => {}
  return { useSourcesContext: () => ({ userId: 8, selectedUser: { type: 'user', id: 8, name: 'X' }, refreshCounts }) }
})

// The panel renders SlackAppWizard, which depends on these hooks; stub them.
vi.mock('./SlackAppWizard', () => ({
  SlackAppWizard: ({ initialApp, initialAppConnected, onCancel, onComplete }: { initialApp?: { id: number } | null; initialAppConnected?: boolean; onCancel?: () => void; onComplete?: () => void }) => (
    <div data-testid="slack-wizard" data-app-id={initialApp?.id ?? ''} data-connected={String(initialAppConnected)}>
      <button onClick={onCancel}>wizard-cancel</button>
      <button onClick={() => onComplete?.()}>wizard-complete</button>
    </div>
  ),
}))

import { SlackPanel } from './SlackPanel'

const workspace = (over: Record<string, unknown> = {}) => ({
  llm_key: { choice: 'default', key_id: null, label: 'personal', provider: 'anthropic', paid_by_you: true, runs: true, can_choose_default: true },
  id: 'W1',
  name: 'Acme',
  domain: 'acme',
  collect_messages: true,
  sync_interval_seconds: 3600,
  last_sync_at: '2026-10-24T10:00:00Z',
  sync_error: null,
  sync_failing_since: null,
  channel_count: 2,
  connected_users: 5,
  user_connected: true,
  poll_blocker: null,
  sync_stale: false,
  apps: [],
  project_id: null,
  sensitivity: 'basic',
  ...over,
})

const channel = (over: Record<string, unknown> = {}) => ({
  id: 'C1',
  workspace_id: 'W1',
  name: 'general',
  channel_type: 'channel',
  is_private: false,
  is_archived: false,
  collect_messages: null,
  effective_collect: true,
  last_message_ts: null,
  project_id: null,
  sensitivity: 'basic',
  ...over,
})

const draftApp = { id: 4, name: 'Acme app', setup_state: 'draft', is_owner: true }

beforeEach(() => {
  vi.clearAllMocks()
  listWorkspaces.mockResolvedValue([])
  listProjects.mockResolvedValue([{ id: 9, title: 'Proj', repo_path: 'o/r' }])
  listChannels.mockResolvedValue([channel()])
  updateWorkspace.mockResolvedValue(workspace())
  deleteWorkspace.mockResolvedValue(undefined)
  triggerSync.mockResolvedValue({ status: 'queued' })
  updateChannel.mockResolvedValue(channel())
  getApp.mockResolvedValue({ id: 4, name: 'Acme app', setup_state: 'draft' })
  listApps.mockResolvedValue([])
  deleteApp.mockResolvedValue(undefined)
})

describe('SlackPanel - load states', () => {
  it('shows loading first', () => {
    listWorkspaces.mockReturnValue(new Promise(() => {}))
    renderWithUser(<SlackPanel />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('shows empty state when no workspaces are connected', async () => {
    renderWithUser(<SlackPanel />)
    expect(await screen.findByText(/No Slack workspaces connected/)).toBeInTheDocument()
  })

  it('shows error state and retries', async () => {
    listWorkspaces.mockRejectedValueOnce(new Error('slack down'))
    const { user } = renderWithUser(<SlackPanel />)
    expect(await screen.findByText('slack down')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(/No Slack workspaces connected/)).toBeInTheDocument()
  })

  it('renders a workspace card with domain, counts, and collecting state', async () => {
    listWorkspaces.mockResolvedValue([workspace()])
    renderWithUser(<SlackPanel />)
    expect(await screen.findByText('Acme')).toBeInTheDocument()
    expect(screen.getByText('acme.slack.com')).toBeInTheDocument()
    expect(screen.getByText('2 channels')).toBeInTheDocument()
    expect(screen.getByText('5 connected users')).toBeInTheDocument()
    expect(screen.getByText('Collecting')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument()
  })

  it.each([
    [{ poll_blocker: 'Slack app 4 is inactive' }, 'Not collecting'],
    [{ sync_error: 'rate limited' }, 'Sync failing'],
    [{ sync_stale: true }, 'Stale'],
    [{ last_sync_at: null }, 'Waiting for first sync'],
  ])('does not claim to be collecting when %o', async (over, status) => {
    listWorkspaces.mockResolvedValue([workspace(over)])
    renderWithUser(<SlackPanel />)
    expect(await screen.findByText(status)).toBeInTheDocument()
    expect(screen.queryByText('Collecting')).not.toBeInTheDocument()
  })

  it('shows a paused workspace with a Resume action', async () => {
    listWorkspaces.mockResolvedValue([workspace({ collect_messages: false, poll_blocker: null })])
    renderWithUser(<SlackPanel />)
    expect(await screen.findByText('Paused')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Resume' })).toBeInTheDocument()
    expect(screen.queryByText('Collecting')).not.toBeInTheDocument()
  })

  it('explains why a collecting workspace is not polled', async () => {
    listWorkspaces.mockResolvedValue([workspace({ poll_blocker: 'Slack app 4 is inactive' })])
    renderWithUser(<SlackPanel />)
    expect(await screen.findByText('Not collecting: Slack app 4 is inactive')).toBeInTheDocument()
  })

  it('shows the workspace sync error', async () => {
    listWorkspaces.mockResolvedValue([workspace({ sync_error: 'rate limited' })])
    renderWithUser(<SlackPanel />)
    expect(await screen.findByText('Sync failing: rate limited')).toBeInTheDocument()
  })
})

describe('SlackPanel - connect wizard', () => {
  it('opens the wizard from the header button', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText(/No Slack workspaces connected/)

    await user.click(screen.getAllByRole('button', { name: 'Connect workspace' })[0])
    expect(screen.getByTestId('slack-wizard')).toBeInTheDocument()
  })

  it('closes the wizard on cancel and lists the app it may have created as unfinished', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText(/No Slack workspaces connected/)

    listApps.mockResolvedValue([{ ...draftApp, id: 6, name: 'Half done' }])
    await user.click(screen.getAllByRole('button', { name: 'Connect workspace' })[0])
    await user.click(screen.getByRole('button', { name: 'wizard-cancel' }))
    expect(screen.queryByTestId('slack-wizard')).not.toBeInTheDocument()
    expect(await screen.findByText('Half done')).toBeInTheDocument()
  })

  it('keeps the same wizard mounted while OAuth completion reloads the panel', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText(/No Slack workspaces connected/)
    await user.click(screen.getAllByRole('button', { name: 'Connect workspace' })[0])
    const wizard = screen.getByTestId('slack-wizard')

    listWorkspaces.mockReturnValue(new Promise(() => {}))
    const channel = new BroadcastChannel('slack-oauth-8')
    channel.postMessage({ type: 'oauth-complete' })
    channel.close()
    await waitFor(() => expect(listWorkspaces).toHaveBeenCalledTimes(2))

    expect(screen.getByTestId('slack-wizard')).toBe(wizard)
    expect(screen.queryByText('Loading...')).not.toBeInTheDocument()
  })

  it('reloads workspaces when the wizard completes', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText(/No Slack workspaces connected/)

    await user.click(screen.getAllByRole('button', { name: 'Connect workspace' })[0])
    await user.click(screen.getByRole('button', { name: 'wizard-complete' }))
    expect(screen.queryByTestId('slack-wizard')).not.toBeInTheDocument()
    await waitFor(() => expect(listWorkspaces).toHaveBeenCalledTimes(2))
  })
})

describe('SlackPanel - workspace actions', () => {
  beforeEach(() => listWorkspaces.mockResolvedValue([workspace()]))

  it('toggles collect_messages', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.click(screen.getByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(updateWorkspace).toHaveBeenCalledWith('W1', { collect_messages: false }))
  })

  it('updates the project assignment', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    const projectSelect = screen.getByTitle('Project')
    await user.selectOptions(projectSelect, '9')
    await waitFor(() => expect(updateWorkspace).toHaveBeenCalledWith('W1', { project_id: 9 }))
  })

  it('hides the project picker and skips the project list while teams is off', async () => {
    renderWithUser(<SlackPanel />, { modules: new Set() })
    await screen.findByText('Acme')

    expect(screen.queryByTitle('Project')).not.toBeInTheDocument()
    expect(screen.getByTitle('Sensitivity')).toBeInTheDocument()
    expect(listProjects).not.toHaveBeenCalled()
  })

  it('updates the sensitivity', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.selectOptions(screen.getByTitle('Sensitivity'), 'internal')
    await waitFor(() => expect(updateWorkspace).toHaveBeenCalledWith('W1', { sensitivity: 'internal' }))
  })

  it('updates the LLM key', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.selectOptions(screen.getByLabelText('LLM key'), 'none')
    await waitFor(() => expect(updateWorkspace).toHaveBeenCalledWith('W1', { llm_key: 'none' }))
  })

  it("announces in the workspace's row why its LLM key didn't change", async () => {
    updateWorkspace.mockRejectedValueOnce(new Error('only the owner can do that'))
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    const select = screen.getByLabelText('LLM key')
    await user.selectOptions(select, 'none')

    const row = screen.getByText('Acme').closest('li')!
    expect(await within(row).findByRole('alert')).toHaveTextContent('only the owner can do that')
    expect(select).toHaveValue('default')
  })

  it('triggers a sync', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.click(screen.getByRole('button', { name: 'Sync now' }))
    await waitFor(() => expect(triggerSync).toHaveBeenCalledWith('W1'))
  })

  it('shows why Sync Now failed', async () => {
    triggerSync.mockRejectedValueOnce(new Error('Nothing can poll this workspace'))
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.click(screen.getByRole('button', { name: 'Sync now' }))
    expect(await screen.findByText('Nothing can poll this workspace')).toBeInTheDocument()
  })

  it('disconnects after confirmation', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.click(screen.getByRole('button', { name: 'Disconnect' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/Disconnect "Acme"\?/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Disconnect' }))
    await waitFor(() => expect(deleteWorkspace).toHaveBeenCalledWith('W1'))
  })

  it('cancels a disconnect', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.click(screen.getByRole('button', { name: 'Disconnect' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(deleteWorkspace).not.toHaveBeenCalled()
  })
})

describe('SlackPanel - event setup', () => {
  it('marks a workspace whose app has no push events as polling only', async () => {
    listWorkspaces.mockResolvedValue([workspace({ apps: [draftApp] })])
    renderWithUser(<SlackPanel />)
    expect(await screen.findByText('Polling only')).toBeInTheDocument()
    expect(screen.getByText('Collecting')).toBeInTheDocument()
  })

  it.each(['live', 'degraded'])('does not mark a %s app as polling only', async (setup_state) => {
    listWorkspaces.mockResolvedValue([workspace({ apps: [{ ...draftApp, setup_state }] })])
    renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')
    expect(screen.queryByText('Polling only')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Finish event setup' })).not.toBeInTheDocument()
  })

  it('resumes the existing app in the setup wizard', async () => {
    listWorkspaces.mockResolvedValue([workspace({ apps: [draftApp] })])
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.click(screen.getByRole('button', { name: 'Finish event setup' }))
    expect(getApp).toHaveBeenCalledWith(4)
    const wizard = await screen.findByTestId('slack-wizard')
    expect(wizard).toHaveAttribute('data-app-id', '4')
    expect(wizard).toHaveAttribute('data-connected', 'true')
  })

  it('lists an app that never reached OAuth so its setup can be resumed instead of duplicated', async () => {
    listApps.mockResolvedValue([{ ...draftApp, id: 6, name: 'Half done' }, draftApp])
    listWorkspaces.mockResolvedValue([workspace({ apps: [draftApp] })])
    const { user } = renderWithUser(<SlackPanel />)

    expect(await screen.findByText('Half done')).toBeInTheDocument()
    expect(screen.getAllByText('Setup incomplete')).toHaveLength(1)
    await user.click(screen.getByRole('button', { name: 'Resume setup' }))
    const wizard = screen.getByTestId('slack-wizard')
    expect(wizard).toHaveAttribute('data-app-id', '6')
    expect(wizard).toHaveAttribute('data-connected', 'false')
  })

  it('offers only Resume setup, not a second Connect, when an unfinished app stands in for the empty state', async () => {
    listApps.mockResolvedValue([{ ...draftApp, id: 6, name: 'Half done' }])
    renderWithUser(<SlackPanel />)

    expect(await screen.findByText('Half done')).toBeInTheDocument()
    expect(screen.queryByText(/No Slack workspaces connected/)).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Connect workspace' })).toHaveLength(1)
  })

  it('deletes an unfinished app after confirmation', async () => {
    listApps.mockResolvedValue([{ ...draftApp, id: 6, name: 'Half done' }])
    const { user } = renderWithUser(<SlackPanel />)

    await user.click(await screen.findByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(deleteApp).toHaveBeenCalledWith(6))
  })

  it('offers no setup action to someone who does not own the app', async () => {
    listWorkspaces.mockResolvedValue([workspace({ apps: [{ ...draftApp, is_owner: false }] })])
    renderWithUser(<SlackPanel />)
    expect(await screen.findByText('Polling only')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Finish event setup' })).not.toBeInTheDocument()
  })
})

describe('SlackPanel - channels', () => {
  beforeEach(() => listWorkspaces.mockResolvedValue([workspace()]))

  it('loads channels lazily on first expand', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    expect(listChannels).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: /Channels \(2\)/ }))
    await waitFor(() => expect(listChannels).toHaveBeenCalledWith('W1'))
    expect(await screen.findByText('general')).toBeInTheDocument()
  })

  it.each([
    [null, 'skip', false],
    [null, 'collect', true],
    [false, 'inherit', null],
  ])('sets a channel with collect_messages %s to %s in one step', async (current, choice, sent) => {
    listChannels.mockResolvedValue([channel({ collect_messages: current })])
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.click(screen.getByRole('button', { name: /Channels \(2\)/ }))
    await screen.findByText('general')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Collect messages' }), choice)
    await waitFor(() => expect(updateChannel).toHaveBeenCalledWith('C1', { collect_messages: sent }))
    expect(updateChannel).toHaveBeenCalledTimes(1)
  })

  it('names what an inheriting channel inherits', async () => {
    listWorkspaces.mockResolvedValue([workspace({ collect_messages: false })])
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.click(screen.getByRole('button', { name: /Channels \(2\)/ }))
    await screen.findByText('general')
    expect(screen.getByRole('option', { name: 'Inherit (skip)' })).toBeInTheDocument()
  })

  it('says so when a workspace has no channels', async () => {
    listChannels.mockResolvedValue([])
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.click(screen.getByRole('button', { name: /Channels \(2\)/ }))
    expect(await screen.findByText('No channels')).toBeInTheDocument()
    expect(screen.queryByText('Loading channels...')).not.toBeInTheDocument()
  })

  it('shows why channels failed to load and retries', async () => {
    listChannels.mockRejectedValueOnce(new Error('slack 503'))
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.click(screen.getByRole('button', { name: /Channels \(2\)/ }))
    expect(await screen.findByText(/slack 503/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('general')).toBeInTheDocument()
  })

  it.each([
    [null, '9', 9],
    [9, '', null],
  ])('changes a channel project from %s to %s', async (current, option, sent) => {
    listChannels.mockResolvedValue([channel({ project_id: current })])
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.click(screen.getByRole('button', { name: /Channels \(2\)/ }))
    await screen.findByText('general')

    const channelRow = screen.getByText('general').closest('li')!
    const selects = within(channelRow).getAllByRole('combobox')
    await user.selectOptions(selects[0], option)
    await waitFor(() => expect(updateChannel).toHaveBeenCalledWith('C1', { project_id: sent }))
  })

  it('disables controls for an archived channel', async () => {
    listChannels.mockResolvedValue([channel({ id: 'C2', name: 'old', is_archived: true })])
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.click(screen.getByRole('button', { name: /Channels \(2\)/ }))
    expect(await screen.findByText('old')).toBeInTheDocument()
    const channelRow = screen.getByText('old').closest('li')!
    expect(within(channelRow).getByText('Archived')).toBeInTheDocument()
    within(channelRow).getAllByRole('combobox').forEach(sel => expect(sel).toBeDisabled())
  })
})

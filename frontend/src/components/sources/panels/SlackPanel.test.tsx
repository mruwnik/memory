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

vi.mock('@/hooks/useProjects', () => ({
  useProjects: () => ({ listProjects }),
}))

vi.mock('../Sources', () => ({
  useSourcesContext: () => ({ userId: 8, selectedUser: { type: 'user', id: 8, name: 'X' } }),
}))

// The panel renders SlackAppWizard, which depends on these hooks; stub them.
vi.mock('./SlackAppWizard', () => ({
  SlackAppWizard: ({ onCancel, onComplete }: { onCancel?: () => void; onComplete?: () => void }) => (
    <div data-testid="slack-wizard">
      <button onClick={onCancel}>wizard-cancel</button>
      <button onClick={() => onComplete?.()}>wizard-complete</button>
    </div>
  ),
}))

import { SlackPanel } from './SlackPanel'

const workspace = (over: Record<string, unknown> = {}) => ({
  id: 'W1',
  name: 'Acme',
  domain: 'acme',
  collect_messages: true,
  sync_interval_seconds: 3600,
  last_sync_at: null,
  sync_error: null,
  channel_count: 2,
  user_count: 5,
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

beforeEach(() => {
  vi.clearAllMocks()
  listWorkspaces.mockResolvedValue([])
  listProjects.mockResolvedValue([{ id: 9, title: 'Proj', repo_path: 'o/r' }])
  listChannels.mockResolvedValue([channel()])
  updateWorkspace.mockResolvedValue(workspace())
  deleteWorkspace.mockResolvedValue(undefined)
  triggerSync.mockResolvedValue({ status: 'queued' })
  updateChannel.mockResolvedValue(channel())
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
    expect(screen.getByText('5 users')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Collecting' })).toBeInTheDocument()
  })

  it('shows the workspace sync error', async () => {
    listWorkspaces.mockResolvedValue([workspace({ sync_error: 'rate limited' })])
    renderWithUser(<SlackPanel />)
    expect(await screen.findByText('Error: rate limited')).toBeInTheDocument()
  })
})

describe('SlackPanel - connect wizard', () => {
  it('opens the wizard from the header button', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText(/No Slack workspaces connected/)

    await user.click(screen.getAllByRole('button', { name: 'Connect Workspace' })[0])
    expect(screen.getByTestId('slack-wizard')).toBeInTheDocument()
  })

  it('closes the wizard on cancel', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText(/No Slack workspaces connected/)

    await user.click(screen.getAllByRole('button', { name: 'Connect Workspace' })[0])
    await user.click(screen.getByRole('button', { name: 'wizard-cancel' }))
    expect(screen.queryByTestId('slack-wizard')).not.toBeInTheDocument()
  })

  it('reloads workspaces when the wizard completes', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText(/No Slack workspaces connected/)

    await user.click(screen.getAllByRole('button', { name: 'Connect Workspace' })[0])
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

    await user.click(screen.getByRole('button', { name: 'Collecting' }))
    await waitFor(() => expect(updateWorkspace).toHaveBeenCalledWith('W1', { collect_messages: false }))
  })

  it('updates the project assignment', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    const projectSelect = screen.getByTitle('Project')
    await user.selectOptions(projectSelect, '9')
    await waitFor(() => expect(updateWorkspace).toHaveBeenCalledWith('W1', { project_id: 9 }))
  })

  it('updates the sensitivity', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.selectOptions(screen.getByTitle('Sensitivity'), 'internal')
    await waitFor(() => expect(updateWorkspace).toHaveBeenCalledWith('W1', { sensitivity: 'internal' }))
  })

  it('triggers a sync', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.click(screen.getByRole('button', { name: 'Sync Now' }))
    await waitFor(() => expect(triggerSync).toHaveBeenCalledWith('W1'))
  })

  it('disconnects after confirmation', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.click(screen.getByRole('button', { name: 'Disconnect' }))
    expect(screen.getByText(/Are you sure you want to disconnect "Acme"/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
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

  it('cycles channel collect state inherit -> on', async () => {
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.click(screen.getByRole('button', { name: /Channels \(2\)/ }))
    await screen.findByText('general')

    // collect_messages is null (inherit) -> next should be true
    const channelRow = screen.getByText('general').closest('div')!.parentElement as HTMLElement
    await user.click(within(channelRow).getByRole('button', { name: /Inherit/ }))
    await waitFor(() => expect(updateChannel).toHaveBeenCalledWith('C1', { collect_messages: true }))
  })

  it('updates a channel project assignment', async () => {
    listChannels.mockResolvedValue([channel()])
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.click(screen.getByRole('button', { name: /Channels \(2\)/ }))
    await screen.findByText('general')

    const channelRow = screen.getByText('general').closest('div')!.parentElement as HTMLElement
    const selects = within(channelRow).getAllByRole('combobox')
    await user.selectOptions(selects[0], '9')
    await waitFor(() => expect(updateChannel).toHaveBeenCalledWith('C1', { project_id: 9 }))
  })

  it('disables controls for an archived channel', async () => {
    listChannels.mockResolvedValue([channel({ id: 'C2', name: 'old', is_archived: true })])
    const { user } = renderWithUser(<SlackPanel />)
    await screen.findByText('Acme')

    await user.click(screen.getByRole('button', { name: /Channels \(2\)/ }))
    expect(await screen.findByText('old (archived)')).toBeInTheDocument()
    const channelRow = screen.getByText('old (archived)').closest('div')!.parentElement as HTMLElement
    within(channelRow).getAllByRole('combobox').forEach(sel => expect(sel).toBeDisabled())
  })
})

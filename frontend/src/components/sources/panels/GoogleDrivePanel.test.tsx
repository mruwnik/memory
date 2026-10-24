import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor, within } from '@/test/utils'

const listGoogleAccounts = vi.fn()
const addGoogleFolder = vi.fn()
const updateGoogleFolder = vi.fn()
const deleteGoogleFolder = vi.fn()
const syncGoogleFolder = vi.fn()
const browseGoogleDrive = vi.fn()
const listProjects = vi.fn()

vi.mock('@/hooks/useSources', () => ({
  useSources: () => ({
    listGoogleAccounts,
    addGoogleFolder,
    updateGoogleFolder,
    deleteGoogleFolder,
    syncGoogleFolder,
    browseGoogleDrive,
  }),
}))

vi.mock('@/hooks/useProjects', () => ({
  useProjects: () => ({ listProjects }),
}))

vi.mock('../Sources', () => ({
  useSourcesContext: () => ({ userId: 3, selectedUser: { type: 'user', id: 3, name: 'X' } }),
}))

import { GoogleDrivePanel } from './GoogleDrivePanel'

const folder = (over: Record<string, unknown> = {}) => ({
  llm_key: { choice: 'default', key_id: null, label: 'personal', provider: 'anthropic', paid_by_you: true, runs: true },
  id: 100,
  folder_id: 'abc',
  folder_name: 'Docs',
  folder_path: '/Docs',
  recursive: true,
  include_shared: false,
  tags: [],
  check_interval: 60,
  last_sync_at: null,
  active: true,
  exclude_folder_ids: [],
  project_id: null,
  sensitivity: 'basic',
  ...over,
})

const acct = (over: Record<string, unknown> = {}) => ({
  id: 1,
  name: 'me',
  email: 'me@example.com',
  active: true,
  last_sync_at: null,
  sync_error: null,
  sync_failing_since: null,
  folders: [],
  ...over,
})

const driveItem = (over: Record<string, unknown> = {}) => ({
  id: 'f1',
  name: 'Folder One',
  mime_type: 'application/vnd.google-apps.folder',
  is_folder: true,
  size: null,
  modified_at: null,
  ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
  listGoogleAccounts.mockResolvedValue([])
  listProjects.mockResolvedValue([{ id: 9, title: 'P', repo_path: 'o/r' }])
  addGoogleFolder.mockResolvedValue(folder())
  updateGoogleFolder.mockResolvedValue(folder())
  deleteGoogleFolder.mockResolvedValue(undefined)
  syncGoogleFolder.mockResolvedValue({ task_id: 't', status: 'q' })
  browseGoogleDrive.mockResolvedValue({
    folder_id: 'root',
    folder_name: 'My Drive',
    parent_id: null,
    items: [driveItem(), driveItem({ id: 'd1', name: 'Doc.txt', is_folder: false })],
    next_page_token: null,
  })
})

describe('GoogleDrivePanel - load states', () => {
  it('shows loading first', () => {
    listGoogleAccounts.mockReturnValue(new Promise(() => {}))
    renderWithRouter(<GoogleDrivePanel />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('shows the "no accounts connected" message when there are none', async () => {
    renderWithRouter(<GoogleDrivePanel />)
    expect(await screen.findByText(/No Google accounts connected/)).toBeInTheDocument()
  })

  it('shows error state and retries', async () => {
    listGoogleAccounts.mockRejectedValueOnce(new Error('drive down'))
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    expect(await screen.findByText('drive down')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(/No Google accounts connected/)).toBeInTheDocument()
  })

  it('renders accounts with no folders configured', async () => {
    listGoogleAccounts.mockResolvedValue([acct()])
    renderWithRouter(<GoogleDrivePanel />)
    expect(await screen.findByText('me@example.com')).toBeInTheDocument()
    expect(screen.getByText('No folders configured for sync')).toBeInTheDocument()
    expect(screen.getByText('Synced folders (0)')).toBeInTheDocument()
  })

  it('renders folder badges and sync errors', async () => {
    listGoogleAccounts.mockResolvedValue([
      acct({
        sync_error: 'token expired', sync_failing_since: new Date(Date.now() - 9 * 86_400_000).toISOString(),
        folders: [folder({
          recursive: true, include_shared: true, exclude_folder_ids: ['x', 'y'],
          sync_error: 'File not found', sync_failing_since: new Date(Date.now() - 3 * 86_400_000).toISOString(),
        })],
      }),
    ])
    renderWithRouter(<GoogleDrivePanel />)
    expect(await screen.findByText('Docs')).toBeInTheDocument()
    expect(screen.getByText(/^Sync failing since [^()]+: token expired$/)).toBeInTheDocument()
    expect(screen.getByText(/^Sync failing since .+ \(3d ago\): File not found$/)).toBeInTheDocument()
    expect(screen.getAllByText('Sync failing')).toHaveLength(2)
    expect(screen.getByText('Recursive')).toBeInTheDocument()
    expect(screen.getByText('Shared')).toBeInTheDocument()
    expect(screen.getByText('2 excluded')).toBeInTheDocument()
  })

  it('shortens a long sync error until asked for the rest', async () => {
    const error = `HttpError 500 ${'backendError '.repeat(40)}end of error`
    listGoogleAccounts.mockResolvedValue([acct({ folders: [folder({ sync_error: error })] })])
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    expect(await screen.findByText(/^Sync failing: HttpError 500 .*…$/)).toBeInTheDocument()
    expect(screen.queryByText(/end of error/)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Show full error' }))
    expect(screen.getByText(`Sync failing: ${error}`)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument()
  })
})

describe('GoogleDrivePanel - folder row actions', () => {
  beforeEach(() => listGoogleAccounts.mockResolvedValue([acct({ folders: [folder()] })]))

  it('pauses a folder', async () => {
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('Docs')
    await user.click(screen.getByRole('button', { name: 'Pause' }))
    await waitFor(() => expect(updateGoogleFolder).toHaveBeenCalledWith(1, 100, { active: false }))
  })

  it('removes a folder', async () => {
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('Docs')
    await user.click(screen.getByRole('button', { name: 'Remove' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(deleteGoogleFolder).toHaveBeenCalledWith(1, 100))
  })

  it('syncs a folder', async () => {
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('Docs')
    await user.click(screen.getByRole('button', { name: 'Sync' }))
    await waitFor(() => expect(syncGoogleFolder).toHaveBeenCalledWith(1, 100))
  })

  it('shows a paused folder as inactive with a Resume action', async () => {
    listGoogleAccounts.mockResolvedValue([acct({ folders: [folder({ active: false })] })])
    renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('Docs')
    expect(screen.getByText('Inactive')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Resume' })).toBeInTheDocument()
  })

  it('edits a folder, including its access control', async () => {
    listGoogleAccounts.mockResolvedValue([acct({ folders: [folder({ tags: ['a'], check_interval: 90 })] })])
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('Docs')
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit folder' })
    expect(within(dialog).queryByPlaceholderText('From Google Drive URL')).not.toBeInTheDocument()
    await user.selectOptions(within(dialog).getByLabelText('Project'), '9')
    await user.selectOptions(within(dialog).getByLabelText('Sensitivity'), 'internal')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(updateGoogleFolder).toHaveBeenCalledWith(1, 100, expect.objectContaining({
      folder_name: 'Docs', tags: ['a'], check_interval: 90, project_id: 9, sensitivity: 'internal',
    })))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('clears a folder project by sending null', async () => {
    listGoogleAccounts.mockResolvedValue([acct({ folders: [folder({ project_id: 9 })] })])
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('Docs')
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit folder' })
    await user.selectOptions(within(dialog).getByLabelText('Project'), '')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(updateGoogleFolder).toHaveBeenCalledWith(1, 100, expect.objectContaining({ project_id: null })))
  })
})

describe('GoogleDrivePanel - Add by ID form', () => {
  beforeEach(() => listGoogleAccounts.mockResolvedValue([acct()]))

  it('submits a folder created from the form fields', async () => {
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('me@example.com')

    await user.click(screen.getByRole('button', { name: 'Add by ID' }))
    await user.type(screen.getByPlaceholderText('From Google Drive URL'), 'fid-123')
    await user.type(screen.getByPlaceholderText('My Documents'), 'My Folder')
    await user.click(screen.getByRole('button', { name: 'Add folder' }))

    await waitFor(() =>
      expect(addGoogleFolder).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ folder_id: 'fid-123', folder_name: 'My Folder', recursive: true }),
      ),
    )
  })

  it('surfaces an error from addGoogleFolder', async () => {
    addGoogleFolder.mockRejectedValueOnce(new Error('dup folder'))
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('me@example.com')

    await user.click(screen.getByRole('button', { name: 'Add by ID' }))
    await user.type(screen.getByPlaceholderText('From Google Drive URL'), 'fid')
    await user.type(screen.getByPlaceholderText('My Documents'), 'F')
    await user.click(screen.getByRole('button', { name: 'Add folder' }))

    expect(await screen.findByText('dup folder')).toBeInTheDocument()
  })

  it('closes the form on Cancel', async () => {
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('me@example.com')

    await user.click(screen.getByRole('button', { name: 'Add by ID' }))
    expect(screen.getByText('Add Google Drive folder')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByText('Add Google Drive folder')).not.toBeInTheDocument()
  })
})

describe('GoogleDrivePanel - Browse & add', () => {
  beforeEach(() => listGoogleAccounts.mockResolvedValue([acct()]))

  it('lists drive items and disables Add Selected with nothing chosen', async () => {
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('me@example.com')

    await user.click(screen.getByRole('button', { name: 'Browse & add' }))
    expect(await screen.findByText('Folder One')).toBeInTheDocument()
    expect(browseGoogleDrive).toHaveBeenCalledWith(1, 'root')
    expect(screen.getByText('0 items selected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add selected' })).toBeDisabled()
  })

  it('selecting an item and adding calls addGoogleFolder per selection', async () => {
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('me@example.com')

    await user.click(screen.getByRole('button', { name: 'Browse & add' }))
    await screen.findByText('Folder One')

    await user.click(screen.getByRole('checkbox', { name: 'Folder One' }))
    expect(screen.getByText('1 item selected')).toBeInTheDocument()
    const dialog = screen.getByRole('dialog')
    await user.selectOptions(within(dialog).getByLabelText('Project'), '9')
    await user.selectOptions(within(dialog).getByLabelText('Sensitivity'), 'confidential')

    await user.click(screen.getByRole('button', { name: 'Add selected' }))
    await waitFor(() =>
      expect(addGoogleFolder).toHaveBeenCalledWith(
        1,
        { folder_id: 'f1', folder_name: 'Folder One', recursive: true, project_id: 9, sensitivity: 'confidential' },
      ),
    )
  })

  it('shows empty message when the folder has no items', async () => {
    browseGoogleDrive.mockResolvedValue({
      folder_id: 'root', folder_name: 'My Drive', parent_id: null, items: [], next_page_token: null,
    })
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('me@example.com')

    await user.click(screen.getByRole('button', { name: 'Browse & add' }))
    expect(await screen.findByText('This folder is empty')).toBeInTheDocument()
  })

  it('shows an error when browsing fails', async () => {
    browseGoogleDrive.mockRejectedValueOnce(new Error('browse failed'))
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('me@example.com')

    await user.click(screen.getByRole('button', { name: 'Browse & add' }))
    expect(await screen.findByText('browse failed')).toBeInTheDocument()
  })

  it('navigates into a subfolder via the arrow button', async () => {
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('me@example.com')

    await user.click(screen.getByRole('button', { name: 'Browse & add' }))
    await screen.findByText('Folder One')

    await user.click(screen.getByRole('button', { name: 'Open folder Folder One' }))
    await waitFor(() => expect(browseGoogleDrive).toHaveBeenLastCalledWith(1, 'f1'))
    const crumbs = within(screen.getByRole('navigation', { name: 'Folder path' }))
    expect(crumbs.getByText('Folder One')).toHaveAttribute('aria-current', 'location')
    await user.click(crumbs.getByRole('button', { name: 'My Drive' }))
    await waitFor(() => expect(browseGoogleDrive).toHaveBeenLastCalledWith(1, 'root'))
  })
})

describe('GoogleDrivePanel - Exclusions', () => {
  beforeEach(() =>
    listGoogleAccounts.mockResolvedValue([
      acct({ folders: [folder({ recursive: true, exclude_folder_ids: ['pre1'] })] }),
    ]),
  )

  it('opens the exclusion browser preloaded with existing exclusions', async () => {
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('Docs')

    await user.click(screen.getByRole('button', { name: 'Exclusions' }))
    expect(await screen.findByText('Manage exclusions: Docs')).toBeInTheDocument()
    expect(screen.getByText('Excluded folders (1)')).toBeInTheDocument()
  })

  it('names a previously excluded folder once browsing reaches it, and removes it', async () => {
    browseGoogleDrive.mockResolvedValue({
      folder_id: 'abc', folder_name: 'Docs', parent_id: null, items: [driveItem({ id: 'pre1', name: 'Old stuff' })], next_page_token: null,
    })
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('Docs')
    await user.click(screen.getByRole('button', { name: 'Exclusions' }))
    await user.click(await screen.findByRole('button', { name: 'Remove exclusion Old stuff' }))
    expect(screen.queryByText('Excluded folders (1)')).not.toBeInTheDocument()
  })

  it('saving sends the set of excluded ids', async () => {
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('Docs')

    await user.click(screen.getByRole('button', { name: 'Exclusions' }))
    await screen.findByText('Folder One')

    // Tick a new subfolder to exclude
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('checkbox', { name: 'Folder One' }))

    await user.click(screen.getByRole('button', { name: 'Save exclusions' }))
    await waitFor(() =>
      expect(updateGoogleFolder).toHaveBeenCalledWith(
        1,
        100,
        { exclude_folder_ids: expect.arrayContaining(['pre1', 'f1']) },
      ),
    )
  })

  it('only lists folders (filters out files) in the exclusion browser', async () => {
    const { user } = renderWithRouter(<GoogleDrivePanel />)
    await screen.findByText('Docs')

    await user.click(screen.getByRole('button', { name: 'Exclusions' }))
    await screen.findByText('Folder One')
    expect(screen.queryByText('Doc.txt')).not.toBeInTheDocument()
  })
})

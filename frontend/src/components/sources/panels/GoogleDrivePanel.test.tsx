import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithUser, screen, waitFor, within } from '@/test/utils'

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
    renderWithUser(<GoogleDrivePanel />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('shows the "no accounts connected" message when there are none', async () => {
    renderWithUser(<GoogleDrivePanel />)
    expect(await screen.findByText(/No Google accounts connected/)).toBeInTheDocument()
  })

  it('shows error state and retries', async () => {
    listGoogleAccounts.mockRejectedValueOnce(new Error('drive down'))
    const { user } = renderWithUser(<GoogleDrivePanel />)
    expect(await screen.findByText('drive down')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(/No Google accounts connected/)).toBeInTheDocument()
  })

  it('renders accounts with no folders configured', async () => {
    listGoogleAccounts.mockResolvedValue([acct()])
    renderWithUser(<GoogleDrivePanel />)
    expect(await screen.findByText('me@example.com')).toBeInTheDocument()
    expect(screen.getByText('No folders configured for sync')).toBeInTheDocument()
    expect(screen.getByText('Synced Folders (0)')).toBeInTheDocument()
  })

  it('renders folder badges and an account sync error', async () => {
    listGoogleAccounts.mockResolvedValue([
      acct({
        sync_error: 'token expired',
        folders: [folder({ recursive: true, include_shared: true, exclude_folder_ids: ['x', 'y'] })],
      }),
    ])
    renderWithUser(<GoogleDrivePanel />)
    expect(await screen.findByText('Docs')).toBeInTheDocument()
    expect(screen.getByText('token expired')).toBeInTheDocument()
    expect(screen.getByText('Recursive')).toBeInTheDocument()
    expect(screen.getByText('Shared')).toBeInTheDocument()
    expect(screen.getByText('2 excluded')).toBeInTheDocument()
  })
})

describe('GoogleDrivePanel - folder row actions', () => {
  beforeEach(() => listGoogleAccounts.mockResolvedValue([acct({ folders: [folder()] })]))

  it('toggles a folder active state (Disable)', async () => {
    const { user } = renderWithUser(<GoogleDrivePanel />)
    await screen.findByText('Docs')
    await user.click(screen.getByRole('button', { name: 'Disable' }))
    await waitFor(() => expect(updateGoogleFolder).toHaveBeenCalledWith(1, 100, { active: false }))
  })

  it('removes a folder', async () => {
    const { user } = renderWithUser(<GoogleDrivePanel />)
    await screen.findByText('Docs')
    await user.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(deleteGoogleFolder).toHaveBeenCalledWith(1, 100))
  })

  it('syncs a folder', async () => {
    const { user } = renderWithUser(<GoogleDrivePanel />)
    await screen.findByText('Docs')
    await user.click(screen.getByRole('button', { name: 'Sync' }))
    await waitFor(() => expect(syncGoogleFolder).toHaveBeenCalledWith(1, 100))
  })

  it('shows the Enable label when a folder is inactive', async () => {
    listGoogleAccounts.mockResolvedValue([acct({ folders: [folder({ active: false })] })])
    renderWithUser(<GoogleDrivePanel />)
    await screen.findByText('Docs')
    expect(screen.getByRole('button', { name: 'Enable' })).toBeInTheDocument()
  })
})

describe('GoogleDrivePanel - Add by ID form', () => {
  beforeEach(() => listGoogleAccounts.mockResolvedValue([acct()]))

  it('submits a folder created from the form fields', async () => {
    const { user } = renderWithUser(<GoogleDrivePanel />)
    await screen.findByText('me@example.com')

    await user.click(screen.getByRole('button', { name: 'Add by ID' }))
    await user.type(screen.getByPlaceholderText('From Google Drive URL'), 'fid-123')
    await user.type(screen.getByPlaceholderText('My Documents'), 'My Folder')
    await user.click(screen.getByRole('button', { name: 'Add Folder' }))

    await waitFor(() =>
      expect(addGoogleFolder).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ folder_id: 'fid-123', folder_name: 'My Folder', recursive: true }),
      ),
    )
  })

  it('surfaces an error from addGoogleFolder', async () => {
    addGoogleFolder.mockRejectedValueOnce(new Error('dup folder'))
    const { user } = renderWithUser(<GoogleDrivePanel />)
    await screen.findByText('me@example.com')

    await user.click(screen.getByRole('button', { name: 'Add by ID' }))
    await user.type(screen.getByPlaceholderText('From Google Drive URL'), 'fid')
    await user.type(screen.getByPlaceholderText('My Documents'), 'F')
    await user.click(screen.getByRole('button', { name: 'Add Folder' }))

    expect(await screen.findByText('dup folder')).toBeInTheDocument()
  })

  it('closes the form on Cancel', async () => {
    const { user } = renderWithUser(<GoogleDrivePanel />)
    await screen.findByText('me@example.com')

    await user.click(screen.getByRole('button', { name: 'Add by ID' }))
    expect(screen.getByText('Add Google Drive Folder')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByText('Add Google Drive Folder')).not.toBeInTheDocument()
  })
})

describe('GoogleDrivePanel - Browse & Add', () => {
  beforeEach(() => listGoogleAccounts.mockResolvedValue([acct()]))

  it('lists drive items and disables Add Selected with nothing chosen', async () => {
    const { user } = renderWithUser(<GoogleDrivePanel />)
    await screen.findByText('me@example.com')

    await user.click(screen.getByRole('button', { name: 'Browse & Add' }))
    expect(await screen.findByText('Folder One')).toBeInTheDocument()
    expect(browseGoogleDrive).toHaveBeenCalledWith(1, 'root')
    expect(screen.getByText('0 items selected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add Selected' })).toBeDisabled()
  })

  it('selecting an item and adding calls addGoogleFolder per selection', async () => {
    const { user } = renderWithUser(<GoogleDrivePanel />)
    await screen.findByText('me@example.com')

    await user.click(screen.getByRole('button', { name: 'Browse & Add' }))
    await screen.findByText('Folder One')

    const checkboxes = screen.getAllByRole('checkbox')
    await user.click(checkboxes[0]) // select the folder
    expect(screen.getByText('1 item selected')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Add Selected' }))
    await waitFor(() =>
      expect(addGoogleFolder).toHaveBeenCalledWith(
        1,
        { folder_id: 'f1', folder_name: 'Folder One', recursive: true },
      ),
    )
  })

  it('shows empty message when the folder has no items', async () => {
    browseGoogleDrive.mockResolvedValue({
      folder_id: 'root', folder_name: 'My Drive', parent_id: null, items: [], next_page_token: null,
    })
    const { user } = renderWithUser(<GoogleDrivePanel />)
    await screen.findByText('me@example.com')

    await user.click(screen.getByRole('button', { name: 'Browse & Add' }))
    expect(await screen.findByText('This folder is empty')).toBeInTheDocument()
  })

  it('shows an error when browsing fails', async () => {
    browseGoogleDrive.mockRejectedValueOnce(new Error('browse failed'))
    const { user } = renderWithUser(<GoogleDrivePanel />)
    await screen.findByText('me@example.com')

    await user.click(screen.getByRole('button', { name: 'Browse & Add' }))
    expect(await screen.findByText('browse failed')).toBeInTheDocument()
  })

  it('navigates into a subfolder via the arrow button', async () => {
    const { user } = renderWithUser(<GoogleDrivePanel />)
    await screen.findByText('me@example.com')

    await user.click(screen.getByRole('button', { name: 'Browse & Add' }))
    await screen.findByText('Folder One')

    const arrow = document.querySelector('button[title="Browse folder"]') as HTMLButtonElement
    await user.click(arrow)
    await waitFor(() => expect(browseGoogleDrive).toHaveBeenLastCalledWith(1, 'f1'))
  })
})

describe('GoogleDrivePanel - Exclusions', () => {
  beforeEach(() =>
    listGoogleAccounts.mockResolvedValue([
      acct({ folders: [folder({ recursive: true, exclude_folder_ids: ['pre1'] })] }),
    ]),
  )

  it('opens the exclusion browser preloaded with existing exclusions', async () => {
    const { user } = renderWithUser(<GoogleDrivePanel />)
    await screen.findByText('Docs')

    await user.click(screen.getByRole('button', { name: 'Exclusions' }))
    expect(await screen.findByText('Manage Exclusions: Docs')).toBeInTheDocument()
    expect(screen.getByText('Excluded Folders (1)')).toBeInTheDocument()
  })

  it('saving sends the set of excluded ids', async () => {
    const { user } = renderWithUser(<GoogleDrivePanel />)
    await screen.findByText('Docs')

    await user.click(screen.getByRole('button', { name: 'Exclusions' }))
    await screen.findByText('Folder One')

    // Tick a new subfolder to exclude
    const dialog = screen.getByRole('dialog')
    const checkboxes = within(dialog).getAllByRole('checkbox')
    await user.click(checkboxes[0])

    await user.click(screen.getByRole('button', { name: 'Save Exclusions' }))
    await waitFor(() =>
      expect(updateGoogleFolder).toHaveBeenCalledWith(
        1,
        100,
        { exclude_folder_ids: expect.arrayContaining(['pre1', 'f1']) },
      ),
    )
  })

  it('only lists folders (filters out files) in the exclusion browser', async () => {
    const { user } = renderWithUser(<GoogleDrivePanel />)
    await screen.findByText('Docs')

    await user.click(screen.getByRole('button', { name: 'Exclusions' }))
    await screen.findByText('Folder One')
    expect(screen.queryByText('Doc.txt')).not.toBeInTheDocument()
  })
})

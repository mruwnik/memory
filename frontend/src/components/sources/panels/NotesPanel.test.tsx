import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithUser, screen } from '@/test/utils'
import { ApiError } from '@/hooks/apiError'
import type { NotesGitSetup, NotesSource } from '@/hooks/useNotesSource'

const getNotesSource = vi.fn()
const setUpNotesGit = vi.fn()
const clearNotesGitToken = vi.fn()
const syncNotes = vi.fn()
const listKeys = vi.fn().mockResolvedValue([])
const setUpNotesSSH = vi.fn()
const probeNotesSSH = vi.fn()
vi.mock('@/hooks/useSSHKeys', () => ({ useSSHKeys: () => ({ list: listKeys }) }))
let scopes: string[] = []

vi.mock('@/hooks/useNotesSource', () => ({
  useNotesSource: () => ({ getNotesSource, setUpNotesGit, clearNotesGitToken, syncNotes, setUpNotesSSH, probeNotesSSH }),
}))

vi.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({ hasScope: (scope: string) => scopes.includes(scope) }),
}))

import { NotesPanel } from './NotesPanel'

const git = (over: Partial<NotesGitSetup> = {}): NotesGitSetup => ({
  has_repo: false,
  branch: null,
  remote_url: null,
  remote_locked: false,
  author_name: null,
  author_email: null,
  token: { set: false, hint: null, locked: false },
  ssh_public_key: 'ssh-ed25519 AAAA memory',
  ...over,
})

const source = (over: Partial<NotesSource> = {}): NotesSource => ({ count: 3, last_sync: null, git: null, ...over })

const FAILED = { ok: false, failed_step: 'push', hint: 'Pushing to the remote failed.', at: '2026-10-25T10:00:00Z' }

beforeEach(() => {
  vi.clearAllMocks()
  scopes = []
  setUpNotesGit.mockImplementation(async () => source({ git: git() }))
  syncNotes.mockResolvedValue(undefined)
})

describe('NotesPanel', () => {
  it('shows members the count and why the last sync failed, without the git setup', async () => {
    getNotesSource.mockResolvedValue(source({ last_sync: FAILED }))
    renderWithUser(<NotesPanel />)

    expect(await screen.findByText('3')).toBeInTheDocument()
    expect(screen.getByText('failed')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Pushing to the remote failed.')
    expect(screen.queryByRole('region', { name: 'Git sync' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sync now' })).not.toBeInTheDocument()
  })

  it('sets up an https remote with a write-only token', async () => {
    scopes = ['*']
    getNotesSource.mockResolvedValue(source({ git: git() }))
    const { user } = renderWithUser(<NotesPanel />)

    await user.type(await screen.findByLabelText('Remote URL'), 'https://git.example.com/me/notes.git')
    await user.type(screen.getByLabelText('Commit author name'), 'Me')
    await user.type(screen.getByLabelText('Commit author email'), 'me@example.com')
    await user.type(screen.getByLabelText('Access token'), 'ghp_secret1234')
    await user.click(screen.getByRole('button', { name: 'Save and sync' }))

    expect(setUpNotesGit).toHaveBeenCalledWith({
      branch: 'main',
      confirm_destination_change: false,
      remote_url: 'https://git.example.com/me/notes.git',
      author_name: 'Me',
      author_email: 'me@example.com',
      token: 'ghp_secret1234',
    })
    expect(await screen.findByText(/The vault syncs in the background/)).toBeInTheDocument()
  })

  it('shows the selected database key for an ssh remote, and the server\'s reason on the remote field', async () => {
    scopes = ['*']
    getNotesSource.mockResolvedValue(source({ git: git({ remote_url: 'git@github.com:me/notes.git', author_name: 'Me', author_email: 'me@example.com', ssh: { key_id: 7, public_key: 'ssh-ed25519 AAAA memory', remote_url: 'git@github.com:me/notes.git', host_keys: 'ssh-ed25519 host' } }) }))
    setUpNotesGit.mockRejectedValue(new ApiError('Use an https:// or ssh:// URL', { remote_url: 'Use an https:// or ssh:// URL' }))
    const { user } = renderWithUser(<NotesPanel />)

    await screen.findByLabelText('Remote URL')
    expect(screen.getByText('ssh-ed25519 AAAA memory')).toBeInTheDocument()
    expect(screen.queryByLabelText('Access token')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save and sync' }))

    expect(await screen.findByLabelText('Remote URL')).toHaveAccessibleDescription(expect.stringContaining('Use an https:// or ssh:// URL'))
  })

  it('keeps the destination when only changing the author', async () => {
    scopes = ['*']
    getNotesSource.mockResolvedValue(source({
      git: git({ has_repo: true, branch: 'master', remote_url: 'https://git.example.com/me/notes.git', remote_locked: true, author_name: 'Old', author_email: 'old@example.com', token: { set: true, hint: '1234', locked: false } }),
    }))
    const { user } = renderWithUser(<NotesPanel />)

    expect(await screen.findByLabelText('Remote URL')).toHaveValue('https://git.example.com/me/notes.git')
    expect(screen.getByLabelText('Access token')).toHaveAccessibleDescription(expect.stringContaining('ends in 1234'))
    await user.clear(screen.getByLabelText('Commit author name'))
    await user.type(screen.getByLabelText('Commit author name'), 'New')
    await user.click(screen.getByRole('button', { name: 'Save and sync' }))

    expect(setUpNotesGit).toHaveBeenCalledWith({ remote_url: 'https://git.example.com/me/notes.git', branch: 'master', confirm_destination_change: false, author_name: 'New', author_email: 'old@example.com', token: null })
  })

  it('allows choosing a destination when the vault has no selected remote', async () => {
    scopes = ['*']
    getNotesSource.mockResolvedValue(source({ git: git({ has_repo: true, remote_locked: true }) }))
    renderWithUser(<NotesPanel />)

    expect(await screen.findByLabelText('Remote URL')).toHaveValue('')
  })

  it('queues a sync now', async () => {
    scopes = ['*']
    getNotesSource.mockResolvedValue(source({ git: git({ has_repo: true, remote_url: 'git@github.com:me/notes.git', remote_locked: true }) }))
    const { user } = renderWithUser(<NotesPanel />)

    await user.click(await screen.findByRole('button', { name: 'Sync now' }))

    expect(syncNotes).toHaveBeenCalledOnce()
    expect(await screen.findByText(/Sync queued/)).toBeInTheDocument()
  })
})


it('requires confirmation before changing the remote and branch', async () => {
  scopes = ['*']
  getNotesSource.mockResolvedValue(source({ git: git({ has_repo: true, branch: 'main', remote_url: 'https://old.example/repo.git', author_name: 'Me', author_email: 'me@example.com' }) }))
  const { user } = renderWithUser(<NotesPanel />)
  const remote = await screen.findByLabelText('Remote URL')
  await user.clear(remote)
  await user.type(remote, 'https://new.example/repo.git')
  await user.clear(screen.getByLabelText('Branch'))
  await user.type(screen.getByLabelText('Branch'), 'notes')
  await user.click(screen.getByRole('button', { name: 'Save and sync' }))
  expect(setUpNotesGit).not.toHaveBeenCalled()
  expect(screen.getByRole('dialog')).toHaveTextContent('entire vault history')
  await user.click(screen.getByRole('button', { name: 'Change destination' }))
  expect(setUpNotesGit).toHaveBeenCalledWith(expect.objectContaining({ remote_url: 'https://new.example/repo.git', branch: 'notes', confirm_destination_change: true }))
})

it.each([null, 1])('selects a database SSH key owned by %s and saves host trust before syncing', async (owner) => {
  scopes = ['*']
  const remote = 'git@example.com:notes.git'
  const ssh = { key_id: 7, public_key: 'ssh-ed25519 public', remote_url: remote, host_keys: 'ssh-ed25519 host' }
  listKeys.mockResolvedValue([{ id: 7, name: 'Database identity', user_id: owner, public_key: ssh.public_key }])
  getNotesSource.mockResolvedValue(source({ git: git({ remote_url: remote }) }))
  setUpNotesSSH.mockResolvedValue(source({ git: git({ remote_url: remote, ssh }) }))
  const { user } = renderWithUser(<NotesPanel />)
  await screen.findByRole('option', { name: 'Database identity' })
  await user.selectOptions(screen.getByLabelText('SSH key'), '7')
  expect(screen.getByRole('button', { name: 'Save and sync' })).toBeDisabled()
  await user.click(screen.getByText('Advanced: enter trusted host keys manually'))
  await user.type(screen.getByLabelText('Trusted SSH host keys'), ssh.host_keys)
  await user.click(screen.getByRole('button', { name: 'Save SSH configuration' }))
  expect(setUpNotesSSH).toHaveBeenCalledWith(remote, ssh.host_keys, 7)
  expect(await screen.findByText('SSH configuration saved.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save and sync' })).toBeEnabled()
})


it('probes without saving trust and saves only after confirmation', async () => {
  scopes = ['*']
  const remote = 'ssh://git@example.com:2222/notes.git'
  listKeys.mockResolvedValue([{ id: 7, name: 'My key', user_id: 1, public_key: 'ssh-ed25519 identity' }])
  getNotesSource.mockResolvedValue(source({ git: git({ remote_url: remote }) }))
  probeNotesSSH.mockResolvedValue({ remote_url: remote, host: 'example.com', port: 2222, keys: [{ algorithm: 'ssh-ed25519', public_key: 'ssh-ed25519 server', fingerprint: 'SHA256:server' }] })
  setUpNotesSSH.mockResolvedValue(source({ git: git({ remote_url: remote, ssh: { key_id: 7, public_key: 'ssh-ed25519 identity', remote_url: remote, host_keys: 'ssh-ed25519 server' } }) }))
  const { user } = renderWithUser(<NotesPanel />)
  await screen.findByRole('option', { name: 'My key' })
  await user.selectOptions(screen.getByLabelText('SSH key'), '7')
  await user.click(screen.getByRole('button', { name: 'Check server' }))
  expect(await screen.findByText('SHA256:server')).toBeInTheDocument()
  expect(probeNotesSSH).toHaveBeenCalledWith(remote)
  expect(setUpNotesSSH).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Trust this server' }))
  expect(setUpNotesSSH).toHaveBeenCalledWith(remote, 'ssh-ed25519 server', 7)
  expect(await screen.findByText('Server trusted. SSH configuration saved.')).toBeInTheDocument()
})

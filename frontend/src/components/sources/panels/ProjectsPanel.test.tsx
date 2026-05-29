import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithUser, screen, waitFor, within } from '@/test/utils'

const listProjects = vi.fn()
const getProjectTree = vi.fn()
const getProject = vi.fn()
const createProject = vi.fn()
const updateProject = vi.fn()
const deleteProject = vi.fn()

const listTeams = vi.fn()
const listMembers = vi.fn()
const listProjectTeams = vi.fn()
const assignTeamToProject = vi.fn()
const unassignTeamFromProject = vi.fn()
const getTeam = vi.fn()

const addJournalEntry = vi.fn()

vi.mock('@/hooks/useProjects', () => ({
  useProjects: () => ({
    listProjects,
    getProjectTree,
    getProject,
    createProject,
    updateProject,
    deleteProject,
  }),
}))

vi.mock('@/hooks/useTeams', () => ({
  useTeams: () => ({
    listTeams,
    listMembers,
    listProjectTeams,
    assignTeamToProject,
    unassignTeamFromProject,
    getTeam,
  }),
}))

vi.mock('@/hooks/useJournal', () => ({
  useJournal: () => ({ addJournalEntry }),
}))

vi.mock('@/hooks/usePeople', () => ({}))

import { ProjectsPanel } from './ProjectsPanel'

const project = (over: Record<string, unknown> = {}) => ({
  id: 1,
  title: 'Alpha',
  description: 'First project',
  state: 'open',
  repo_path: null,
  github_id: null,
  number: null,
  parent_id: null,
  children_count: 0,
  owner_id: null,
  due_on: null,
  doc_url: null,
  owner: null,
  teams: [],
  ...over,
})

const adminTeam = {
  id: 100,
  name: 'Admin',
  slug: 'admin',
  members: [{ id: 1, identifier: 'alice', display_name: 'Alice' }],
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  listProjects.mockResolvedValue([])
  getProjectTree.mockResolvedValue([])
  getProject.mockResolvedValue({ project: project(), journal_entries: [] })
  getTeam.mockImplementation(async (slug: string) =>
    slug === 'admin' ? adminTeam : { id: 101, name: 'Internal', slug: 'internal', members: [] },
  )
  listTeams.mockResolvedValue([{ id: 5, name: 'Team A', slug: 'team-a' }])
  listMembers.mockResolvedValue([])
  listProjectTeams.mockResolvedValue([])
  createProject.mockResolvedValue({ success: true, project: project() })
  updateProject.mockResolvedValue({ success: true, project: project() })
  deleteProject.mockResolvedValue({ success: true, deleted_id: 1 })
  assignTeamToProject.mockResolvedValue({ success: true })
  unassignTeamFromProject.mockResolvedValue({ success: true })
  addJournalEntry.mockResolvedValue({ status: 'ok', entry: { id: 1, content: 'x', created_at: '2024-01-01T00:00:00Z' } })
})

describe('ProjectsPanel - load states', () => {
  it('shows loading first', () => {
    listProjects.mockReturnValue(new Promise(() => {}))
    renderWithUser(<ProjectsPanel />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('shows empty state when there are no projects', async () => {
    renderWithUser(<ProjectsPanel />)
    expect(await screen.findByText(/No projects found/)).toBeInTheDocument()
  })

  it('shows error state and retries', async () => {
    listProjects.mockRejectedValueOnce(new Error('projects down'))
    const { user } = renderWithUser(<ProjectsPanel />)
    expect(await screen.findByText('projects down')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(/No projects found/)).toBeInTheDocument()
  })

  it('renders a project card (list view) with state badge', async () => {
    listProjects.mockResolvedValue([project()])
    renderWithUser(<ProjectsPanel />)
    expect(await screen.findByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText('First project')).toBeInTheDocument()
    expect(screen.getByText('open')).toBeInTheDocument()
  })

  it('refetches with the chosen state filter', async () => {
    listProjects.mockResolvedValue([project()])
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.selectOptions(screen.getByDisplayValue('Open'), 'closed')
    await waitFor(() =>
      expect(listProjects).toHaveBeenLastCalledWith({ state: 'closed', include_teams: true, limit: 500 }),
    )
  })

  it('filters the list by the search query', async () => {
    listProjects.mockResolvedValue([project(), project({ id: 2, title: 'Beta' })])
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.type(screen.getByPlaceholderText('Search projects...'), 'Beta')
    expect(screen.queryByText('Alpha')).not.toBeInTheDocument()
    expect(screen.getByText('Beta')).toBeInTheDocument()
  })
})

describe('ProjectsPanel - create flow', () => {
  it('requires at least one team', async () => {
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText(/No projects found/)

    await user.click(screen.getByRole('button', { name: 'Create Project' }))
    await user.type(screen.getByPlaceholderText('Project name'), 'New')
    await user.click(screen.getByRole('button', { name: 'Create' }))

    expect(await screen.findByText('Please select at least one team')).toBeInTheDocument()
    expect(createProject).not.toHaveBeenCalled()
  })

  it('creates a project with selected team and ISO due date', async () => {
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText(/No projects found/)

    await user.click(screen.getByRole('button', { name: 'Create Project' }))
    await user.type(screen.getByPlaceholderText('Project name'), 'New Proj')
    await user.click(screen.getByLabelText(/Team A/))
    await user.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() =>
      expect(createProject).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'New Proj', team_ids: [5], state: 'open' }),
      ),
    )
  })
})

describe('ProjectsPanel - edit flow', () => {
  it('edits a standalone project sending updated fields', async () => {
    listProjects.mockResolvedValue([project()])
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const titleInput = screen.getByPlaceholderText('Project name')
    await user.clear(titleInput)
    await user.type(titleInput, 'Alpha Renamed')
    await user.click(screen.getByRole('button', { name: 'Update' }))

    await waitFor(() =>
      expect(updateProject).toHaveBeenCalledWith(1, expect.objectContaining({ title: 'Alpha Renamed' })),
    )
  })

  it('restricts editable fields for GitHub-backed projects', async () => {
    listProjects.mockResolvedValue([project({ repo_path: 'org/repo', number: 3 })])
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(screen.getByText(/This project is synced from GitHub/)).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Project name')).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Update' }))
    await waitFor(() => expect(updateProject).toHaveBeenCalledTimes(1))
    const payload = updateProject.mock.calls[0][1]
    expect(payload).not.toHaveProperty('title')
    expect(payload).toHaveProperty('parent_id')
  })
})

describe('ProjectsPanel - delete flow', () => {
  it('blocks deleting GitHub-backed projects with an explainer modal', async () => {
    listProjects.mockResolvedValue([project({ repo_path: 'org/repo' })])
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(await screen.findByText('Cannot Delete')).toBeInTheDocument()
    expect(deleteProject).not.toHaveBeenCalled()
  })

  it('deletes a standalone project after confirmation', async () => {
    listProjects.mockResolvedValue([project()])
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(deleteProject).toHaveBeenCalledWith(1))
  })

  it('warns about orphaning children in the confirm message', async () => {
    listProjects.mockResolvedValue([project({ children_count: 2 })])
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(screen.getByText(/2 child project\(s\) will become top-level/)).toBeInTheDocument()
  })
})

describe('ProjectsPanel - teams modal', () => {
  beforeEach(() => {
    listProjects.mockResolvedValue([project()])
    listProjectTeams.mockResolvedValue([{ id: 5, name: 'Team A', slug: 'team-a' }])
    listTeams.mockResolvedValue([
      { id: 5, name: 'Team A', slug: 'team-a' },
      { id: 6, name: 'Team B', slug: 'team-b' },
    ])
  })

  it('lists assigned + available teams and assigns one', async () => {
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: 'Teams' }))
    expect(await screen.findByText('Assigned Teams (1)')).toBeInTheDocument()
    expect(screen.getByText('Team B')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(assignTeamToProject).toHaveBeenCalledWith(1, 6))
  })

  it('unassigns an assigned team and shows an error when it fails', async () => {
    unassignTeamFromProject.mockResolvedValueOnce({ success: false, error: 'last team' })
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: 'Teams' }))
    await screen.findByText('Assigned Teams (1)')

    await user.click(screen.getByRole('button', { name: 'Remove' }))
    expect(await screen.findByText('last team')).toBeInTheDocument()
  })
})

describe('ProjectsPanel - inline owner/due-date editing', () => {
  beforeEach(() => listProjects.mockResolvedValue([project()]))

  it('changes the owner via the owner popover', async () => {
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    // "None" owner chip is clickable; admin team member Alice is selectable
    await user.click(screen.getByTitle('No owner - click to assign'))
    await user.click(await screen.findByRole('button', { name: 'Alice' }))

    await waitFor(() => expect(updateProject).toHaveBeenCalledWith(1, { owner_id: 1 }))
  })

  it('sets a due date via the due-date popover', async () => {
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByTitle('Click to set due date'))
    const dateInput = document.querySelector('input[type="date"]') as HTMLInputElement
    await user.type(dateInput, '2030-01-15')
    await user.click(screen.getByRole('button', { name: 'Apply' }))

    await waitFor(() =>
      expect(updateProject).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ clear_due_on: false }),
      ),
    )
  })
})

describe('ProjectsPanel - tree view', () => {
  it('shows the Tree/List toggle and renders nested children', async () => {
    listProjects.mockResolvedValue([project(), project({ id: 2, title: 'Child', parent_id: 1 })])
    getProjectTree.mockResolvedValue([
      {
        id: 1, title: 'Alpha', description: null, state: 'open', doc_url: null, repo_path: null, parent_id: null,
        children: [
          { id: 2, title: 'Child', description: null, state: 'open', doc_url: null, repo_path: null, parent_id: 1, children: [] },
        ],
      },
    ])
    renderWithUser(<ProjectsPanel />)
    expect(await screen.findByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText('Child')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Tree' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'List' })).toBeInTheDocument()
  })

  it('persists the view mode to localStorage when switching to list', async () => {
    listProjects.mockResolvedValue([project(), project({ id: 2, title: 'Child', parent_id: 1 })])
    getProjectTree.mockResolvedValue([
      {
        id: 1, title: 'Alpha', description: null, state: 'open', doc_url: null, repo_path: null, parent_id: null,
        children: [
          { id: 2, title: 'Child', description: null, state: 'open', doc_url: null, repo_path: null, parent_id: 1, children: [] },
        ],
      },
    ])
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: 'List' }))
    await waitFor(() => expect(localStorage.getItem('projects-view-mode')).toBe('list'))
  })
})

describe('ProjectsPanel - journal', () => {
  it('loads journal entries and adds a new one', async () => {
    listProjects.mockResolvedValue([project()])
    getProject.mockResolvedValue({
      project: project(),
      journal_entries: [{ id: 1, content: 'Existing note', created_at: '2024-01-01T00:00:00Z' }],
    })
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: /Journal/ }))
    expect(await screen.findByText('Existing note')).toBeInTheDocument()

    await user.type(screen.getByPlaceholderText('Add a journal entry...'), 'New entry')
    await user.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(addJournalEntry).toHaveBeenCalledWith(1, 'New entry', 'project'))
  })
})

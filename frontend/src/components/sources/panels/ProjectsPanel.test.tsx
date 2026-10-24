import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderWithUser, screen, waitFor, within } from '@/test/utils'

const listProjects = vi.fn()
const getProjectTree = vi.fn()
const getProject = vi.fn()
const createProject = vi.fn()
const updateProject = vi.fn()
const deleteProject = vi.fn()

const listTeams = vi.fn()
const listProjectTeams = vi.fn()
const assignTeamToProject = vi.fn()
const unassignTeamFromProject = vi.fn()

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
    listProjectTeams,
    assignTeamToProject,
    unassignTeamFromProject,
  }),
}))

vi.mock('@/hooks/useJournal', () => ({
  useJournal: () => ({ addJournalEntry }),
}))

const listPeople = vi.fn()

vi.mock('@/hooks/usePeople', () => ({
  usePeople: () => ({ listPeople }),
}))

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


beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  listProjects.mockResolvedValue([])
  getProjectTree.mockResolvedValue([])
  getProject.mockResolvedValue({ project: project(), journal_entries: [] })
  listPeople.mockResolvedValue([{ id: 1, identifier: 'alice', display_name: 'Alice', aliases: [], contact_info: {}, tags: [], created_at: null }])
  listTeams.mockResolvedValue([{ id: 5, name: 'Team A', slug: 'team-a' }])
  listProjectTeams.mockResolvedValue([])
  createProject.mockResolvedValue(project())
  updateProject.mockResolvedValue(project())
  deleteProject.mockResolvedValue(undefined)
  assignTeamToProject.mockResolvedValue(undefined)
  unassignTeamFromProject.mockResolvedValue(undefined)
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

    await user.click(screen.getAllByRole('button', { name: 'New project' })[0])
    await user.type(screen.getByPlaceholderText('Project name'), 'New')
    await user.click(screen.getByRole('button', { name: 'Create project' }))

    expect(screen.getByLabelText('Teams')).toHaveAccessibleDescription(
      'Required: the teams whose members can access this project. Select at least one team')
    expect(createProject).not.toHaveBeenCalled()
  })

  it('creates a project with selected team and ISO due date', async () => {
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText(/No projects found/)

    await user.click(screen.getAllByRole('button', { name: 'New project' })[0])
    await user.type(screen.getByPlaceholderText('Project name'), 'New Proj')
    await user.type(screen.getByLabelText('Due date'), '2030-01-15')
    await user.click(screen.getByLabelText('Teams'))
    await user.click(screen.getByRole('option', { name: /Team A/ }))
    await user.click(screen.getByRole('button', { name: 'Create project' }))

    await waitFor(() =>
      expect(createProject).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'New Proj', team_ids: [5], state: 'open', due_on: '2030-01-15T00:00:00Z' }),
      ),
    )
  })

  it('keeps the form open with the reason when creating fails', async () => {
    createProject.mockRejectedValueOnce(new Error('Team 5 not found'))
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText(/No projects found/)

    await user.click(screen.getAllByRole('button', { name: 'New project' })[0])
    await user.type(screen.getByPlaceholderText('Project name'), 'New Proj')
    await user.click(screen.getByLabelText('Teams'))
    await user.click(screen.getByRole('option', { name: /Team A/ }))
    await user.click(screen.getByRole('button', { name: 'Create project' }))

    expect(await within(screen.getByRole('dialog')).findByText('Team 5 not found')).toBeInTheDocument()
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
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() =>
      expect(updateProject).toHaveBeenCalledWith(1, expect.objectContaining({ title: 'Alpha Renamed' })),
    )
  })

  it('searches people by name to pick the owner', async () => {
    listProjects.mockResolvedValue([project()])
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.type(screen.getByRole('combobox', { name: 'Owner' }), 'ali')
    await user.click(await screen.findByRole('option', { name: 'Alice' }, { timeout: 2000 }))
    expect(listPeople).toHaveBeenCalledWith({ search: 'ali', limit: 10 })
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(updateProject).toHaveBeenCalledWith(1, expect.objectContaining({ owner_id: 1 })))
  })

  it('shows the current owner and clears it', async () => {
    listProjects.mockResolvedValue([project({ owner_id: 7, owner: { id: 7, identifier: 'zed', display_name: 'Zed' } })])
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(screen.getByRole('combobox', { name: 'Owner' })).toHaveValue('Zed')
    await user.click(screen.getByRole('button', { name: 'Clear owner' }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(updateProject).toHaveBeenCalledWith(1, expect.objectContaining({ owner_id: null })))
  })

  it('restricts editable fields for GitHub-backed projects', async () => {
    listProjects.mockResolvedValue([project({ repo_path: 'org/repo', number: 3 })])
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(screen.getByText(/This project is synced from GitHub/)).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Project name')).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(updateProject).toHaveBeenCalledTimes(1))
    const payload = updateProject.mock.calls[0][1]
    expect(payload).not.toHaveProperty('title')
    expect(payload).toHaveProperty('parent_id')
  })
})

describe('ProjectsPanel - delete flow', () => {
  it('disables Delete for GitHub-backed projects', async () => {
    listProjects.mockResolvedValue([project({ repo_path: 'org/repo' })])
    renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    expect(screen.getByRole('button', { name: 'Delete' })).toBeDisabled()
  })

  it('deletes a standalone project after confirmation', async () => {
    listProjects.mockResolvedValue([project()])
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(deleteProject).toHaveBeenCalledWith(1))
  })

  it('shows why a delete failed', async () => {
    deleteProject.mockRejectedValueOnce(new Error('Project has open tasks'))
    listProjects.mockResolvedValue([project()])
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))

    expect(await screen.findByText('Project has open tasks')).toBeInTheDocument()
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
    expect(await screen.findByText('Assigned teams (1)')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Add Team B' }))
    await waitFor(() => expect(assignTeamToProject).toHaveBeenCalledWith(1, 6))
  })

  it('unassigns a team after confirmation and shows an error when it fails', async () => {
    unassignTeamFromProject.mockRejectedValueOnce(new Error('last team'))
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: 'Teams' }))
    await screen.findByText('Assigned teams (1)')

    await user.click(screen.getByRole('button', { name: 'Remove Team A' }))
    const dialogs = screen.getAllByRole('dialog')
    await user.click(within(dialogs[dialogs.length - 1]).getByRole('button', { name: 'Remove' }))

    expect(unassignTeamFromProject).toHaveBeenCalledWith(1, 5)
    expect(await screen.findByText('last team')).toBeInTheDocument()
  })
})

describe('ProjectsPanel - due dates', () => {
  afterEach(() => {
    vi.useRealTimers()
    process.env.TZ = 'UTC'
  })

  it('shows the picked calendar day west of UTC, and is not overdue until that day ends', async () => {
    process.env.TZ = 'America/Los_Angeles'
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 9, 30, 20, 0))  // 8pm on the due day, already Oct 31 in UTC
    listProjects.mockResolvedValue([project({ due_on: '2026-10-30T00:00:00Z' })])
    renderWithUser(<ProjectsPanel />)

    expect(await screen.findByText('Due: Oct 30, 2026')).toBeInTheDocument()
    expect(screen.getByText('Due soon')).toBeInTheDocument()
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
  it('fetches nothing until the journal is expanded', async () => {
    listProjects.mockResolvedValue([project(), project({ id: 2, title: 'Beta' })])
    renderWithUser(<ProjectsPanel />)
    await screen.findByText('Beta')

    expect(getProject).not.toHaveBeenCalled()
  })

  it('shows why the journal failed to load', async () => {
    listProjects.mockResolvedValue([project()])
    getProject.mockRejectedValueOnce(new Error('forbidden'))
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: /Journal/ }))

    expect(await screen.findByText("Couldn't load the journal: forbidden")).toBeInTheDocument()
  })

  it('keeps the text and shows why adding an entry failed', async () => {
    listProjects.mockResolvedValue([project()])
    addJournalEntry.mockRejectedValueOnce(new Error('denied'))
    const { user } = renderWithUser(<ProjectsPanel />)
    await screen.findByText('Alpha')

    await user.click(screen.getByRole('button', { name: /Journal/ }))
    await user.type(await screen.findByPlaceholderText('Add a journal entry...'), 'New entry')
    await user.click(screen.getByRole('button', { name: 'Add' }))

    expect(await screen.findByText("Couldn't add the entry: denied")).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Add a journal entry...')).toHaveValue('New entry')
  })

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

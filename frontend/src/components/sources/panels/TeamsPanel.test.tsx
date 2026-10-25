import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithUser, screen, waitFor, within } from '@/test/utils'

const listTeams = vi.fn()
const getTeam = vi.fn()
const createTeam = vi.fn()
const updateTeam = vi.fn()
const addMember = vi.fn()
const removeMember = vi.fn()
const assignTeamToProject = vi.fn()
const unassignTeamFromProject = vi.fn()
const listPeople = vi.fn()
const listProjects = vi.fn()
const getPerson = vi.fn()

vi.mock('@/hooks/useTeams', () => ({
  useTeams: () => ({
    listTeams,
    getTeam,
    createTeam,
    updateTeam,
    addMember,
    removeMember,
    assignTeamToProject,
    unassignTeamFromProject,
  }),
}))

vi.mock('@/hooks/usePeople', () => ({
  usePeople: () => ({ listPeople, getPerson }),
}))

vi.mock('@/hooks/useProjects', () => ({
  useProjects: () => ({ listProjects }),
}))

import { TeamsPanel } from './TeamsPanel'

const person = (over: Record<string, unknown> = {}) => ({
  id: 1,
  identifier: 'alice',
  display_name: 'Alice',
  aliases: [],
  contact_info: {},
  tags: [],
  created_at: null,
  ...over,
})

const team = (over: Record<string, unknown> = {}) => ({
  id: 10,
  name: 'Engineering',
  slug: 'engineering',
  description: 'Core eng',
  owner_id: null,
  owner: null,
  tags: [],
  member_count: 2,
  is_active: true,
  members: [],
  projects: [],
  ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
  listTeams.mockResolvedValue([])
  listPeople.mockResolvedValue([person()])
  listProjects.mockResolvedValue([])
  getTeam.mockResolvedValue(team())
  createTeam.mockResolvedValue({ team: team() })
  updateTeam.mockResolvedValue({ team: team() })
  addMember.mockResolvedValue(undefined)
  removeMember.mockResolvedValue(undefined)
  assignTeamToProject.mockResolvedValue(undefined)
  unassignTeamFromProject.mockResolvedValue(undefined)
})

describe('TeamsPanel - projects off', () => {
  it('hides the Projects button and project summary', async () => {
    listTeams.mockResolvedValue([team({ projects: [{ id: 1, title: 'Alpha', state: 'open', repo_path: null }] })])
    renderWithUser(<TeamsPanel />, { modules: new Set(['teams']) })
    await screen.findByText('Engineering')

    expect(screen.getByRole('button', { name: 'Members' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Projects' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Alpha/)).not.toBeInTheDocument()
    expect(listTeams).toHaveBeenCalledWith(expect.objectContaining({ include_projects: false }))
  })
})

describe('TeamsPanel - load states', () => {
  it('shows loading first', () => {
    listTeams.mockReturnValue(new Promise(() => {}))
    renderWithUser(<TeamsPanel />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('shows empty state when there are no teams', async () => {
    renderWithUser(<TeamsPanel />)
    expect(await screen.findByText(/No teams found/)).toBeInTheDocument()
  })

  it('shows error state and retries', async () => {
    listTeams.mockRejectedValueOnce(new Error('teams down'))
    const { user } = renderWithUser(<TeamsPanel />)
    expect(await screen.findByText('teams down')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText(/No teams found/)).toBeInTheDocument()
  })

  it('renders a team card with name, slug and member count', async () => {
    listTeams.mockResolvedValue([team()])
    renderWithUser(<TeamsPanel />)
    expect(await screen.findByText('Engineering')).toBeInTheDocument()
    expect(screen.getByText('@engineering')).toBeInTheDocument()
    expect(screen.getByText('2 members')).toBeInTheDocument()
  })

  it('passes include_inactive when "Show archived" is toggled', async () => {
    listTeams.mockResolvedValue([team()])
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByLabelText('Show archived'))
    await waitFor(() =>
      expect(listTeams).toHaveBeenLastCalledWith({ include_inactive: true, include_projects: true }),
    )
  })
})

const searchAndAdd = async (user: ReturnType<typeof renderWithUser>['user'], label: string, name: string) => {
  await user.type(screen.getByLabelText(label), name.toLowerCase())
  await user.click(await screen.findByRole('button', { name: `Add ${name}` }, { timeout: 2000 }))
}

const openEdit = async (user: ReturnType<typeof renderWithUser>['user']) => {
  await screen.findByText('Engineering')
  await user.click(screen.getByRole('button', { name: 'Edit' }))
}

const confirmIn = async (user: ReturnType<typeof renderWithUser>['user'], label: string) => {
  await user.click(screen.getByRole('button', { name: label }))
  const dialogs = screen.getAllByRole('dialog')
  await user.click(within(dialogs[dialogs.length - 1]).getByRole('button', { name: 'Remove' }))
}

describe('TeamsPanel - create flow', () => {
  beforeEach(() => listTeams.mockResolvedValue([team()]))

  it('auto-generates the slug from the name and creates the team', async () => {
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'New team' }))
    await user.type(screen.getByPlaceholderText('Engineering Core'), 'My New Team')

    const slugInput = screen.getByPlaceholderText('engineering-core') as HTMLInputElement
    await waitFor(() => expect(slugInput.value).toBe('my-new-team'))

    await user.click(screen.getByRole('button', { name: 'Create team' }))
    await waitFor(() =>
      expect(createTeam).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'My New Team', slug: 'my-new-team' }),
      ),
    )
  })

  it('shows an error when team creation fails', async () => {
    createTeam.mockRejectedValueOnce(new Error('slug exists'))
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'New team' }))
    await user.type(screen.getByPlaceholderText('Engineering Core'), 'Dup')
    await user.click(screen.getByRole('button', { name: 'Create team' }))

    expect(await screen.findByText('slug exists')).toBeInTheDocument()
  })

  it('adds pending members after creating the team', async () => {
    listPeople.mockResolvedValue([person({ id: 2, identifier: 'bob', display_name: 'Bob' })])
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'New team' }))
    await user.type(screen.getByPlaceholderText('Engineering Core'), 'Team X')
    createTeam.mockResolvedValueOnce({ team: team({ slug: 'team-x' }) })
    await searchAndAdd(user, 'Initial members', 'Bob')

    await user.click(screen.getByRole('button', { name: 'Create team' }))
    await waitFor(() => expect(addMember).toHaveBeenCalledWith('team-x', 'bob'))
  })

  it("doesn't submit the form when Enter is pressed in the member search", async () => {
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'New team' }))
    await user.type(screen.getByPlaceholderText('Engineering Core'), 'Team X')
    await user.type(screen.getByLabelText('Initial members'), 'bob{Enter}')

    expect(createTeam).not.toHaveBeenCalled()
  })

  it('reports initial members that could not be added', async () => {
    listPeople.mockResolvedValue([person({ id: 2, identifier: 'bob', display_name: 'Bob' })])
    addMember.mockRejectedValueOnce(new Error('not allowed'))
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'New team' }))
    await user.type(screen.getByPlaceholderText('Engineering Core'), 'Team X')
    await searchAndAdd(user, 'Initial members', 'Bob')
    await user.click(screen.getByRole('button', { name: 'Create team' }))

    expect(await screen.findByText(/Couldn't add Bob: not allowed/)).toBeInTheDocument()
  })

  it('shows warnings returned by the save', async () => {
    createTeam.mockResolvedValueOnce({ team: team(), warnings: ['Owner not found: zed'] })
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'New team' }))
    await user.type(screen.getByPlaceholderText('Engineering Core'), 'Team X')
    await user.click(screen.getByRole('button', { name: 'Create team' }))

    expect(await screen.findByText(/Owner not found: zed/)).toBeInTheDocument()
  })
})

describe('TeamsPanel - edit flow', () => {
  beforeEach(() => listTeams.mockResolvedValue([team()]))

  it('hides the slug field and updates the team', async () => {
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(screen.queryByPlaceholderText('engineering-core')).not.toBeInTheDocument()

    const nameInput = screen.getByPlaceholderText('Engineering Core')
    await user.clear(nameInput)
    await user.type(nameInput, 'Engineering Renamed')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() =>
      expect(updateTeam).toHaveBeenCalledWith(
        'engineering',
        expect.objectContaining({ name: 'Engineering Renamed' }),
      ),
    )
  })

  it('shows an error when update fails', async () => {
    updateTeam.mockRejectedValueOnce(new Error('nope'))
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('nope')).toBeInTheDocument()
  })

  it('saves a cleared description and tags', async () => {
    listTeams.mockResolvedValue([team({ tags: ['eng'] })])
    const { user } = renderWithUser(<TeamsPanel />)
    await openEdit(user)

    await user.clear(screen.getByLabelText('Description'))
    await user.clear(screen.getByLabelText('Tags'))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(updateTeam).toHaveBeenCalledWith('engineering', expect.objectContaining({ description: '', tags: [] })))
  })

  it('searches people by name to pick the owner', async () => {
    listPeople.mockResolvedValue([person({ id: 2, identifier: 'bob', display_name: 'Bob' })])
    const { user } = renderWithUser(<TeamsPanel />)
    await openEdit(user)

    await user.type(screen.getByRole('combobox', { name: 'Owner' }), 'bo')
    await user.click(await screen.findByRole('option', { name: 'Bob' }, { timeout: 2000 }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(updateTeam).toHaveBeenCalledWith('engineering', expect.objectContaining({ owner: 2 })))
  })

  it('clears the owner with an explicit null', async () => {
    listTeams.mockResolvedValue([team({ owner_id: 2, owner: { id: 2, identifier: 'bob', display_name: 'Bob' } })])
    const { user } = renderWithUser(<TeamsPanel />)
    await openEdit(user)

    expect(screen.getByRole('combobox', { name: 'Owner' })).toHaveValue('Bob')
    await user.click(screen.getByRole('button', { name: 'Clear owner' }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(updateTeam).toHaveBeenCalledWith('engineering', expect.objectContaining({ owner: null })))
  })

})

describe('TeamsPanel - archive flow', () => {
  beforeEach(() => listTeams.mockResolvedValue([team()]))

  it('archives a team after confirmation', async () => {
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Archive' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/Archive "Engineering"\?/)).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Archive' }))

    await waitFor(() => expect(updateTeam).toHaveBeenCalledWith('engineering', { is_active: false }))
  })

  it('shows why archiving failed and keeps the list', async () => {
    updateTeam.mockRejectedValueOnce(new Error('Only team leads and admins can modify an existing team'))
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Archive' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Archive' }))

    expect(await screen.findByText('Only team leads and admins can modify an existing team')).toBeInTheDocument()
    expect(screen.getByText('Engineering')).toBeInTheDocument()
  })

  it('cancels archiving', async () => {
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Archive' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(updateTeam).not.toHaveBeenCalled()
  })

  it('hides the Archive action for inactive teams', async () => {
    listTeams.mockResolvedValue([team({ is_active: false })])
    renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')
    expect(screen.queryByRole('button', { name: 'Archive' })).not.toBeInTheDocument()
    expect(screen.getByText('Archived')).toBeInTheDocument()
  })
})

describe('TeamsPanel - members modal', () => {
  beforeEach(() => {
    listTeams.mockResolvedValue([team()])
    getTeam.mockResolvedValue(team({ members: [{ id: 1, identifier: 'alice', display_name: 'Alice', role: 'lead' }] }))
  })

  it('loads current members and adds a searched person', async () => {
    listPeople.mockResolvedValue([person({ id: 2, identifier: 'bob', display_name: 'Bob' })])
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Members' }))
    expect(await screen.findByText('Members (1)')).toBeInTheDocument()

    await searchAndAdd(user, 'Add member', 'Bob')
    await waitFor(() => expect(addMember).toHaveBeenCalledWith('engineering', 'bob'))
  })

  it('removes a member only after confirmation', async () => {
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Members' }))
    await screen.findByText('Members (1)')

    await confirmIn(user, 'Remove Alice')
    await waitFor(() => expect(removeMember).toHaveBeenCalledWith('engineering', 'alice'))
  })

  it('shows an error when adding a member fails', async () => {
    addMember.mockRejectedValueOnce(new Error('already a member'))
    listPeople.mockResolvedValue([person({ id: 2, identifier: 'bob', display_name: 'Bob' })])
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Members' }))
    await screen.findByText('Members (1)')
    await searchAndAdd(user, 'Add member', 'Bob')

    expect(await screen.findByText('already a member')).toBeInTheDocument()
  })

  it('shows a member\'s details when their row is selected', async () => {
    getPerson.mockResolvedValue(person({ aliases: ['Al'], contact_info: { email: 'alice@x.com' } }))
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Members' }))
    await user.click(await screen.findByRole('button', { name: 'Alice' }))

    expect(await screen.findByText('alice@x.com')).toBeInTheDocument()
    expect(getPerson).toHaveBeenCalledWith('alice', true)
  })

  it('offers a retry when the members fail to load', async () => {
    getTeam.mockRejectedValueOnce(new Error('teams down'))
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Members' }))
    expect(await screen.findByText("Couldn't load the members: teams down")).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByText('Members (1)')).toBeInTheDocument()
  })

  it('shows a failed people search instead of an empty result', async () => {
    listPeople.mockRejectedValue(new Error('people down'))
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Members' }))
    await screen.findByText('Members (1)')
    await user.type(screen.getByLabelText('Add member'), 'bob')

    expect(await screen.findByText('Search failed: people down', {}, { timeout: 2000 })).toBeInTheDocument()
  })
})

describe('TeamsPanel - projects modal', () => {
  beforeEach(() => {
    listTeams.mockResolvedValue([team()])
    getTeam.mockResolvedValue(team({ projects: [{ id: 50, title: 'Assigned', state: 'open', repo_path: null }] }))
    listProjects.mockResolvedValue([
      { id: 50, title: 'Assigned', state: 'open', repo_path: null },
      { id: 51, title: 'Available', state: 'open', repo_path: 'o/r' },
    ])
  })

  it('lists assigned and available projects and assigns one', async () => {
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Projects' }))
    expect(await screen.findByText('Assigned projects (1)')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Add Available' }))
    await waitFor(() => expect(assignTeamToProject).toHaveBeenCalledWith(51, 10))
  })

  it('unassigns an assigned project', async () => {
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Projects' }))
    await screen.findByText('Assigned projects (1)')

    await confirmIn(user, 'Remove Assigned')
    await waitFor(() => expect(unassignTeamFromProject).toHaveBeenCalledWith(50, 10))
  })
})

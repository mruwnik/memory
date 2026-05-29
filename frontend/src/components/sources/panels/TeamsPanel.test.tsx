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
const getPerson = vi.fn()
const listProjects = vi.fn()

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
  discord_role_id: null,
  github_team_id: null,
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
  getPerson.mockResolvedValue(person())
  createTeam.mockResolvedValue({ success: true, team: team() })
  updateTeam.mockResolvedValue({ success: true, team: team() })
  addMember.mockResolvedValue({ success: true })
  removeMember.mockResolvedValue({ success: true })
  assignTeamToProject.mockResolvedValue({ success: true })
  unassignTeamFromProject.mockResolvedValue({ success: true })
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

// The search-result rows render display name + @identifier + "Add" inside one
// button; match by the person's name substring to avoid brittle name concat.
const clickPersonResult = async (
  user: ReturnType<typeof renderWithUser>['user'],
  name: string,
) => {
  const btn = await screen.findByRole(
    'button',
    {
      name: (_n, el) =>
        !!el.textContent?.includes(name) && !!el.textContent?.includes('Add'),
    },
    { timeout: 2000 },
  )
  await user.click(btn)
}

describe('TeamsPanel - create flow', () => {
  beforeEach(() => listTeams.mockResolvedValue([team()]))

  it('auto-generates the slug from the name and creates the team', async () => {
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'New Team' }))
    await user.type(screen.getByPlaceholderText('Engineering Core'), 'My New Team')

    const slugInput = screen.getByPlaceholderText('engineering-core') as HTMLInputElement
    await waitFor(() => expect(slugInput.value).toBe('my-new-team'))

    await user.click(screen.getByRole('button', { name: 'Create Team' }))
    await waitFor(() =>
      expect(createTeam).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'My New Team', slug: 'my-new-team' }),
      ),
    )
  })

  it('shows an error when team creation fails', async () => {
    createTeam.mockResolvedValueOnce({ success: false, error: 'slug exists' })
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'New Team' }))
    await user.type(screen.getByPlaceholderText('Engineering Core'), 'Dup')
    await user.click(screen.getByRole('button', { name: 'Create Team' }))

    expect(await screen.findByText('slug exists')).toBeInTheDocument()
  })

  it('adds pending members after creating the team', async () => {
    listPeople.mockResolvedValue([person({ id: 2, identifier: 'bob', display_name: 'Bob' })])
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'New Team' }))
    await user.type(screen.getByPlaceholderText('Engineering Core'), 'Team X')
    await user.type(screen.getByPlaceholderText('Search for people to add...'), 'bob')
    await clickPersonResult(user, 'Bob')

    await user.click(screen.getByRole('button', { name: 'Create Team' }))
    await waitFor(() => expect(createTeam).toHaveBeenCalled())
    await waitFor(() => expect(addMember).toHaveBeenCalledWith('team-x', 'bob'))
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
    await user.click(screen.getByRole('button', { name: 'Save Changes' }))

    await waitFor(() =>
      expect(updateTeam).toHaveBeenCalledWith(
        'engineering',
        expect.objectContaining({ name: 'Engineering Renamed' }),
      ),
    )
  })

  it('shows an error when update fails', async () => {
    updateTeam.mockResolvedValueOnce({ success: false, error: 'nope' })
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Edit' }))
    await user.click(screen.getByRole('button', { name: 'Save Changes' }))
    expect(await screen.findByText('nope')).toBeInTheDocument()
  })
})

describe('TeamsPanel - archive flow', () => {
  beforeEach(() => listTeams.mockResolvedValue([team()]))

  it('archives a team after confirmation', async () => {
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Archive' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/Are you sure you want to archive "Engineering"/)).toBeInTheDocument()
    // The dialog renders its own "Archive Team" title and an "Archive" confirm button.
    expect(within(dialog).getByText('Archive Team')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Archive' }))

    await waitFor(() => expect(updateTeam).toHaveBeenCalledWith('engineering', { is_active: false }))
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
    expect(await screen.findByText('Current Members (1)')).toBeInTheDocument()

    await user.type(screen.getByPlaceholderText('Search for people...'), 'bob')
    await clickPersonResult(user, 'Bob')
    await waitFor(() => expect(addMember).toHaveBeenCalledWith('engineering', 'bob'))
  })

  it('removes a member', async () => {
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Members' }))
    await screen.findByText('Current Members (1)')

    await user.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(removeMember).toHaveBeenCalledWith('engineering', 'alice'))
  })

  it('shows an error when adding a member fails', async () => {
    addMember.mockResolvedValueOnce({ success: false, error: 'already a member' })
    listPeople.mockResolvedValue([person({ id: 2, identifier: 'bob', display_name: 'Bob' })])
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Members' }))
    await screen.findByText('Current Members (1)')
    await user.type(screen.getByPlaceholderText('Search for people...'), 'bob')
    await clickPersonResult(user, 'Bob')

    expect(await screen.findByText('already a member')).toBeInTheDocument()
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
    expect(await screen.findByText('Assigned Projects (1)')).toBeInTheDocument()
    expect(screen.getByText('Available')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Add' }))
    await waitFor(() => expect(assignTeamToProject).toHaveBeenCalledWith(51, 10))
  })

  it('unassigns an assigned project', async () => {
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByRole('button', { name: 'Projects' }))
    await screen.findByText('Assigned Projects (1)')

    await user.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(unassignTeamFromProject).toHaveBeenCalledWith(50, 10))
  })
})

describe('TeamsPanel - owner change', () => {
  beforeEach(() => listTeams.mockResolvedValue([team()]))

  it('assigns an owner via the owner popover', async () => {
    const { user } = renderWithUser(<TeamsPanel />)
    await screen.findByText('Engineering')

    await user.click(screen.getByText('None'))
    await user.click(await screen.findByRole('button', { name: 'Alice' }))
    await waitFor(() => expect(updateTeam).toHaveBeenCalledWith('engineering', { owner: 1 }))
  })
})

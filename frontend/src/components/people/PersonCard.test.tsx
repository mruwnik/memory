import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, within } from '@/test/utils'
import userEvent from '@testing-library/user-event'
import PersonCard from './PersonCard'
import type { Person, Tidbit } from '../../hooks/usePeople'
import type { Team } from '../../hooks/useTeams'

const makePerson = (overrides: Partial<Person> = {}): Person => ({
  id: 1,
  identifier: 'alice_chen',
  display_name: 'Alice Chen',
  aliases: [],
  contact_info: {},
  tags: [],
  created_at: null,
  ...overrides,
})

const makeTidbit = (overrides: Partial<Tidbit> = {}): Tidbit => ({
  id: 1,
  person_id: 1,
  content: 'Likes coffee',
  tidbit_type: null,
  source: null,
  sensitivity: null,
  project_id: null,
  tags: [],
  creator_id: null,
  created_at: null,
  ...overrides,
})

const makeTeam = (overrides: Partial<Team> = {}): Team => ({
  id: 1,
  name: 'Engineering',
  slug: 'eng',
  description: null,
  owner_id: null,
  owner: null,
  tags: [],
  is_active: true,
  created_at: null,
  archived_at: null,
  ...overrides,
})

const baseProps = {
  expanded: false,
  onToggleExpand: vi.fn(),
  onEdit: vi.fn(),
  onDelete: vi.fn(),
  onAddTidbit: vi.fn(),
  canEditTidbit: () => true,
  onEditTidbit: vi.fn(),
  onDeleteTidbit: vi.fn(),
}

beforeEach(() => {
  baseProps.onToggleExpand = vi.fn()
  baseProps.onEdit = vi.fn()
  baseProps.onDelete = vi.fn()
})

describe('PersonCard basic rendering', () => {
  it('renders display name, identifier handle, and avatar initial', () => {
    render(<PersonCard {...baseProps} person={makePerson()} />)
    expect(screen.getByText('Alice Chen')).toBeInTheDocument()
    expect(screen.getByText('@alice_chen')).toBeInTheDocument()
    expect(screen.getByText('A')).toBeInTheDocument()
  })

  it('renders tags when present', () => {
    render(<PersonCard {...baseProps} person={makePerson({ tags: ['work', 'vip'] })} />)
    expect(screen.getByText('work')).toBeInTheDocument()
    expect(screen.getByText('vip')).toBeInTheDocument()
  })

  it('does not render a tag list when tags are absent', () => {
    render(<PersonCard {...baseProps} person={makePerson({ tags: [] })} />)
    expect(screen.queryByText('work')).not.toBeInTheDocument()
  })
})

describe('PersonCard actions', () => {
  it('invokes onEdit when Edit is clicked', async () => {
    const user = userEvent.setup()
    render(<PersonCard {...baseProps} person={makePerson()} />)
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(baseProps.onEdit).toHaveBeenCalledTimes(1)
  })

  it('invokes onDelete once Delete is confirmed', async () => {
    const user = userEvent.setup()
    render(<PersonCard {...baseProps} person={makePerson()} />)
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    expect(baseProps.onDelete).not.toHaveBeenCalled()
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))
    expect(baseProps.onDelete).toHaveBeenCalledTimes(1)
  })
})

describe('PersonCard expand affordance', () => {
  it('toggles expand from the name even when nothing is loaded yet', async () => {
    const user = userEvent.setup()
    render(<PersonCard {...baseProps} person={makePerson()} />)
    await user.click(screen.getByRole('button', { name: 'Alice Chen' }))
    expect(baseProps.onToggleExpand).toHaveBeenCalledTimes(1)
  })

  it('does not toggle when clicking the Edit button', async () => {
    const user = userEvent.setup()
    render(<PersonCard {...baseProps} person={makePerson({ aliases: ['ali'] })} />)
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    expect(baseProps.onToggleExpand).not.toHaveBeenCalled()
  })

  it('says there are no tidbits when an expanded person has none', () => {
    render(<PersonCard {...baseProps} person={makePerson()} expanded tidbits={[]} />)
    expect(screen.getByText('No tidbits yet')).toBeInTheDocument()
  })
})

describe('PersonCard expanded details', () => {
  it('does not render expanded section when collapsed', () => {
    render(
      <PersonCard {...baseProps} person={makePerson({ aliases: ['ali'] })} expanded={false} />,
    )
    expect(screen.queryByText('Also known as')).not.toBeInTheDocument()
  })

  it('renders aliases when expanded', () => {
    render(
      <PersonCard
        {...baseProps}
        person={makePerson({ aliases: ['ali', 'achen'] })}
        expanded
      />,
    )
    expect(screen.getByText('Also known as')).toBeInTheDocument()
    expect(screen.getByText('ali')).toBeInTheDocument()
    expect(screen.getByText('achen')).toBeInTheDocument()
  })

  it('renders email and phone as mailto/tel links and skips non-string contact values', () => {
    render(
      <PersonCard
        {...baseProps}
        person={makePerson({
          contact_info: {
            email: 'a@x.com',
            phone: '+1 555-1234',
            website: 'https://a.dev',
            // non-string value should be skipped
            slack: { workspace: 'w' } as unknown as string,
          },
        })}
        expanded
      />,
    )
    expect(screen.getByText('Email')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'a@x.com' })).toHaveAttribute('href', 'mailto:a@x.com')
    expect(screen.getByRole('link', { name: '+1 555-1234' })).toHaveAttribute('href', 'tel:+1 555-1234')
    expect(screen.getByText('https://a.dev')).toBeInTheDocument()
    expect(screen.queryByText(/workspace/)).not.toBeInTheDocument()
  })

  it('shows teams loading state', () => {
    render(
      <PersonCard
        {...baseProps}
        person={makePerson({ aliases: ['ali'] })}
        expanded
        teamsLoading
      />,
    )
    expect(screen.getByText('Loading teams...')).toBeInTheDocument()
  })

  it('renders the person\'s teams', () => {
    render(
      <PersonCard
        {...baseProps}
        person={makePerson()}
        expanded
        teams={[
          makeTeam({ id: 1, name: 'Eng' }),
          makeTeam({ id: 2, name: 'Ops' }),
        ]}
      />,
    )
    expect(screen.getByText('Eng')).toBeInTheDocument()
    expect(screen.getByText('Ops')).toBeInTheDocument()
  })

  it('shows tidbits loading state', () => {
    render(
      <PersonCard
        {...baseProps}
        person={makePerson({ aliases: ['ali'] })}
        expanded
        tidbitsLoading
      />,
    )
    expect(screen.getByText('Loading tidbits...')).toBeInTheDocument()
  })

  it('renders tidbits with count, type, source and tags; hides basic sensitivity', () => {
    render(
      <PersonCard
        {...baseProps}
        person={makePerson()}
        expanded
        tidbits={[
          makeTidbit({
            id: 1,
            content: 'Likes coffee',
            tidbit_type: 'preference',
            source: 'chat',
            sensitivity: 'basic',
            tags: ['food'],
          }),
        ]}
      />,
    )
    expect(screen.getByText('Tidbits (1)')).toBeInTheDocument()
    expect(screen.getByText('Likes coffee')).toBeInTheDocument()
    expect(screen.getByText('preference')).toBeInTheDocument()
    expect(screen.getByText('chat')).toBeInTheDocument()
    expect(screen.getByText('food')).toBeInTheDocument()
    // basic sensitivity is not rendered
    expect(screen.queryByText('basic')).not.toBeInTheDocument()
  })

  it.each([
    ['confidential', 'confidential'],
    ['internal', 'internal'],
  ])('renders %s sensitivity badge', (_label, sensitivity) => {
    render(
      <PersonCard
        {...baseProps}
        person={makePerson()}
        expanded
        tidbits={[makeTidbit({ sensitivity })]}
      />,
    )
    expect(screen.getByText(sensitivity)).toBeInTheDocument()
  })

  it('renders created date when present', () => {
    render(
      <PersonCard
        {...baseProps}
        person={makePerson({ aliases: ['ali'], created_at: '2024-01-15T00:00:00Z' })}
        expanded
      />,
    )
    expect(screen.getByText(/Added/)).toBeInTheDocument()
  })

  it('reveals details only after expanding (has details)', () => {
    const { rerender } = render(
      <PersonCard {...baseProps} person={makePerson({ aliases: ['ali'] })} expanded={false} />,
    )
    expect(screen.queryByText('Also known as')).not.toBeInTheDocument()
    rerender(<PersonCard {...baseProps} person={makePerson({ aliases: ['ali'] })} expanded />)
    expect(screen.getByText('Also known as')).toBeInTheDocument()
  })
})

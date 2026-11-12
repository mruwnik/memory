import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@/test/utils'
import userEvent from '@testing-library/user-event'
import PersonFormModal from './PersonFormModal'
import type { Person } from '../../hooks/usePeople'

const makePerson = (overrides: Partial<Person> = {}): Person => ({
  id: 1,
  identifier: 'alice_chen',
  display_name: 'Alice Chen',
  aliases: ['ali'],
  contact_info: { email: 'a@x.com', phone: '555' },
  tags: ['work'],
  created_at: null,
  ...overrides,
})

const baseProps = {
  title: 'New person',
  onSubmit: vi.fn().mockResolvedValue(undefined),
  onCancel: vi.fn(),
  loading: false,
  error: null as string | null,
  submitLabel: 'Create person',
}

beforeEach(() => {
  baseProps.onSubmit = vi.fn().mockResolvedValue(undefined)
  baseProps.onCancel = vi.fn()
})

describe('PersonFormModal rendering', () => {
  it('renders dialog with title and required fields', () => {
    render(<PersonFormModal {...baseProps} />)
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true')
    expect(screen.getByRole('heading', { name: 'New person' })).toBeInTheDocument()
    expect(screen.getByLabelText(/Display name/)).toBeInTheDocument()
    expect(screen.getByLabelText(/Identifier/)).toBeInTheDocument()
  })

  it('renders an error alert when error is provided', () => {
    render(<PersonFormModal {...baseProps} error="Something failed" />)
    expect(screen.getByRole('alert')).toHaveTextContent('Something failed')
  })

  it('disables submit while loading and shows Saving label', () => {
    render(<PersonFormModal {...baseProps} loading />)
    expect(screen.getByRole('button', { name: 'Saving...' })).toBeDisabled()
  })

  it('disables submit when display_name or identifier empty', () => {
    render(<PersonFormModal {...baseProps} />)
    expect(screen.getByRole('button', { name: 'Create person' })).toBeDisabled()
  })
})

describe('PersonFormModal identifier auto-generation (create)', () => {
  it('auto-generates a slug identifier from the display name', async () => {
    const user = userEvent.setup()
    render(<PersonFormModal {...baseProps} />)
    await user.type(screen.getByLabelText(/Display name/), 'Bob Smith')
    expect(screen.getByLabelText(/Identifier/)).toHaveValue('bob_smith')
  })

  it('strips diacritics and punctuation in the generated identifier', async () => {
    const user = userEvent.setup()
    render(<PersonFormModal {...baseProps} />)
    await user.type(screen.getByLabelText(/Display name/), 'José! O’Brien')
    expect(screen.getByLabelText(/Identifier/)).toHaveValue('jose_obrien')
  })

  it('stops auto-generating once the identifier is manually edited', async () => {
    const user = userEvent.setup()
    render(<PersonFormModal {...baseProps} />)
    const idInput = screen.getByLabelText(/Identifier/)
    await user.type(screen.getByLabelText(/Display name/), 'Bob')
    await user.clear(idInput)
    await user.type(idInput, 'custom_id')
    await user.type(screen.getByLabelText(/Display name/), ' Smith')
    expect(idInput).toHaveValue('custom_id')
  })
})

describe('PersonFormModal create submission', () => {
  it('submits PersonCreate with parsed comma-separated fields and contact info', async () => {
    const user = userEvent.setup()
    render(<PersonFormModal {...baseProps} />)
    await user.type(screen.getByLabelText(/Display name/), 'Bob Smith')
    await user.type(screen.getByLabelText(/Aliases/), 'bobby , b.smith , ')
    await user.type(screen.getByLabelText(/Tags/), 'work, eng')
    await user.type(screen.getByLabelText('Email'), 'bob@x.com')
    await user.type(screen.getByLabelText('GitHub'), '@bsmith')
    await user.type(screen.getByLabelText(/Add a note/), 'met at conf')
    await user.click(screen.getByRole('button', { name: 'Create person' }))

    expect(baseProps.onSubmit).toHaveBeenCalledTimes(1)
    expect(baseProps.onSubmit).toHaveBeenCalledWith({
      identifier: 'bob_smith',
      display_name: 'Bob Smith',
      aliases: ['bobby', 'b.smith'],
      tags: ['work', 'eng'],
      content: 'met at conf',
      contact_info: { email: 'bob@x.com', github: '@bsmith' },
    })
  })

  it('omits contact_info and content when empty', async () => {
    const user = userEvent.setup()
    render(<PersonFormModal {...baseProps} />)
    await user.type(screen.getByLabelText(/Display name/), 'Bob')
    await user.click(screen.getByRole('button', { name: 'Create person' }))
    expect(baseProps.onSubmit).toHaveBeenCalledWith({
      identifier: 'bob',
      display_name: 'Bob',
      aliases: [],
      tags: [],
      content: undefined,
      contact_info: undefined,
    })
  })
})

describe('PersonFormModal edit mode', () => {
  it('prefills from initialData and disables the identifier field', () => {
    render(
      <PersonFormModal
        {...baseProps}
        title="Edit person"
        submitLabel="Save changes"
        isEdit
        initialData={makePerson()}
      />,
    )
    expect(screen.getByLabelText(/Display name/)).toHaveValue('Alice Chen')
    const idInput = screen.getByLabelText(/Identifier/)
    expect(idInput).toHaveValue('alice_chen')
    expect(idInput).toBeDisabled()
    expect(screen.getByLabelText(/Aliases/)).toHaveValue('ali')
    expect(screen.getByLabelText(/Tags/)).toHaveValue('work')
    expect(screen.getByLabelText('Email')).toHaveValue('a@x.com')
  })

  it('submits PersonUpdate with removed aliases as null', async () => {
    const user = userEvent.setup()
    render(
      <PersonFormModal
        {...baseProps}
        title="Edit person"
        submitLabel="Save changes"
        isEdit
        initialData={makePerson()}
      />,
    )
    await user.clear(screen.getByLabelText('Aliases'))
    await user.type(screen.getByLabelText('Aliases'), 'al2')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(baseProps.onSubmit).toHaveBeenCalledWith({
      display_name: 'Alice Chen',
      aliases: { al2: true, ali: null },
      tags: ['work'],
      content: undefined,
      contact_info: {},
    })
  })

  it('sends only changed contact fields, a cleared one as null so it is removed', async () => {
    const user = userEvent.setup()
    render(<PersonFormModal {...baseProps} submitLabel="Save changes" isEdit initialData={makePerson()} />)
    await user.clear(screen.getByLabelText('Phone'))
    await user.type(screen.getByLabelText('GitHub'), 'octo')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(baseProps.onSubmit.mock.calls[0][0].contact_info).toEqual({ phone: null, github: 'octo' })
  })

  describe('with a merged person whose email is a list', () => {
    const renderMerged = () => {
      const person = makePerson({ contact_info: { email: ['a@x.com', 'b@x.com'], phone: '555' } })
      render(<PersonFormModal {...baseProps} submitLabel="Save changes" isEdit initialData={person} />)
    }

    it('shows each entry as its own removable chip', () => {
      renderMerged()
      expect(screen.getByRole('button', { name: 'Remove a@x.com' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Remove b@x.com' })).toBeInTheDocument()
      expect(screen.queryByDisplayValue(/a@x.com,b@x.com/)).not.toBeInTheDocument()
    })

    it('sends nothing for an untouched list', async () => {
      const user = userEvent.setup()
      renderMerged()
      await user.click(screen.getByRole('button', { name: 'Save changes' }))
      expect(baseProps.onSubmit.mock.calls[0][0].contact_info).toEqual({})
    })

    it('sends a removed entry as null and an added one as true', async () => {
      const user = userEvent.setup()
      renderMerged()
      await user.click(screen.getByRole('button', { name: 'Remove b@x.com' }))
      await user.type(screen.getByLabelText('Email'), 'c@x.com{Enter}')
      await user.click(screen.getByRole('button', { name: 'Save changes' }))
      expect(baseProps.onSubmit.mock.calls[0][0].contact_info).toEqual({ email: { 'b@x.com': null, 'c@x.com': true } })
    })
  })

  it('asks for an identifier when the name has no ASCII letters to build one from', async () => {
    const user = userEvent.setup()
    render(<PersonFormModal {...baseProps} />)
    await user.type(screen.getByLabelText(/Display name/), '山田')
    expect(screen.getByLabelText(/Identifier/)).toHaveAccessibleDescription(/type an identifier/i)
  })

  it('does not auto-generate identifier from name in edit mode', async () => {
    const user = userEvent.setup()
    render(
      <PersonFormModal
        {...baseProps}
        isEdit
        initialData={makePerson({ identifier: 'fixed_id' })}
      />,
    )
    await user.clear(screen.getByLabelText(/Display name/))
    await user.type(screen.getByLabelText(/Display name/), 'New name')
    expect(screen.getByLabelText(/Identifier/)).toHaveValue('fixed_id')
  })
})

describe('PersonFormModal Discord and Slack', () => {
  const slackPerson = () => makePerson({
    contact_info: {
      email: 'a@x.com',
      discord: 4242424242,
      slack: { T1: { user_id: 'U1', username: 'alice', display_name: 'Alice C' } },
    },
  })
  const renderEdit = (person: Person) =>
    render(<PersonFormModal {...baseProps} submitLabel="Save changes" isEdit initialData={person} />)

  it('creates a person with a Discord id and a Slack team/user pair', async () => {
    const user = userEvent.setup()
    render(<PersonFormModal {...baseProps} />)
    await user.type(screen.getByLabelText(/Display name/), 'Bob')
    await user.type(screen.getByLabelText('Discord user id'), '42')
    await user.type(screen.getByLabelText('Slack team id'), 'T9')
    await user.type(screen.getByLabelText('Slack user id'), 'U9')
    await user.click(screen.getByRole('button', { name: 'Create person' }))
    expect(baseProps.onSubmit.mock.calls[0][0].contact_info).toEqual({ discord: '42', slack: { T9: { user_id: 'U9' } } })
  })

  it('prefills a numeric Discord id and sends nothing for untouched Slack and Discord', async () => {
    const user = userEvent.setup()
    renderEdit(slackPerson())
    expect(screen.getByLabelText('Discord user id')).toHaveValue('4242424242')
    expect(screen.getByText('Alice C')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(baseProps.onSubmit.mock.calls[0][0].contact_info).toEqual({})
  })

  it('sends a removed team as null and an added team as its user id', async () => {
    const user = userEvent.setup()
    renderEdit(slackPerson())
    await user.click(screen.getByRole('button', { name: 'Remove' }))
    await user.type(screen.getByLabelText('Slack team id'), 'T2')
    await user.type(screen.getByLabelText('Slack user id'), 'U2')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(baseProps.onSubmit.mock.calls[0][0].contact_info).toEqual({
      slack: { T1: null, T2: { user_id: 'U2', username: null, display_name: null } },
    })
  })

  it("replacing a team's user id clears the old user's names", async () => {
    const user = userEvent.setup()
    renderEdit(slackPerson())
    await user.type(screen.getByLabelText('Slack team id'), 'T1')
    await user.type(screen.getByLabelText('Slack user id'), 'U7')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(baseProps.onSubmit.mock.calls[0][0].contact_info).toEqual({
      slack: { T1: { user_id: 'U7', username: null, display_name: null } },
    })
  })

  it('blocks saving half a Slack pair', async () => {
    const user = userEvent.setup()
    renderEdit(slackPerson())
    await user.type(screen.getByLabelText('Slack team id'), 'T2')
    expect(screen.getByLabelText('Slack user id')).toHaveAccessibleDescription('Enter the user id too')
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
  })

  it('leaves a merged list of Slack accounts read-only', async () => {
    const user = userEvent.setup()
    renderEdit(makePerson({ contact_info: { slack: [{ T1: { user_id: 'U1' } }, { T2: { user_id: 'U2' } }] } }))
    expect(screen.getByText(/can't edit/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Slack team id')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(baseProps.onSubmit.mock.calls[0][0].contact_info).toEqual({})
  })
})

describe('PersonFormModal interactions', () => {
  it('calls onCancel when Cancel button clicked', async () => {
    const user = userEvent.setup()
    render(<PersonFormModal {...baseProps} />)
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(baseProps.onCancel).toHaveBeenCalledTimes(1)
  })

  it('calls onCancel on Escape key', async () => {
    const user = userEvent.setup()
    render(<PersonFormModal {...baseProps} />)
    await user.keyboard('{Escape}')
    expect(baseProps.onCancel).toHaveBeenCalledTimes(1)
  })

  it('focuses the first field on mount', () => {
    render(<PersonFormModal {...baseProps} />)
    expect(screen.getByLabelText(/Display name/)).toHaveFocus()
  })
})

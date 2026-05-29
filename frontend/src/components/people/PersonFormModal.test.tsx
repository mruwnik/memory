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
  title: 'Add New Person',
  onSubmit: vi.fn().mockResolvedValue(undefined),
  onCancel: vi.fn(),
  loading: false,
  error: null as string | null,
  submitLabel: 'Add Person',
}

beforeEach(() => {
  baseProps.onSubmit = vi.fn().mockResolvedValue(undefined)
  baseProps.onCancel = vi.fn()
})

describe('PersonFormModal rendering', () => {
  it('renders dialog with title and required fields', () => {
    render(<PersonFormModal {...baseProps} />)
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true')
    expect(screen.getByRole('heading', { name: 'Add New Person' })).toBeInTheDocument()
    expect(screen.getByLabelText(/Display Name/)).toBeInTheDocument()
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
    expect(screen.getByRole('button', { name: 'Add Person' })).toBeDisabled()
  })
})

describe('PersonFormModal identifier auto-generation (create)', () => {
  it('auto-generates a slug identifier from the display name', async () => {
    const user = userEvent.setup()
    render(<PersonFormModal {...baseProps} />)
    await user.type(screen.getByLabelText(/Display Name/), 'Bob Smith')
    expect(screen.getByLabelText(/Identifier/)).toHaveValue('bob_smith')
  })

  it('strips diacritics and punctuation in the generated identifier', async () => {
    const user = userEvent.setup()
    render(<PersonFormModal {...baseProps} />)
    await user.type(screen.getByLabelText(/Display Name/), 'José! O’Brien')
    expect(screen.getByLabelText(/Identifier/)).toHaveValue('jose_obrien')
  })

  it('stops auto-generating once the identifier is manually edited', async () => {
    const user = userEvent.setup()
    render(<PersonFormModal {...baseProps} />)
    const idInput = screen.getByLabelText(/Identifier/)
    await user.type(screen.getByLabelText(/Display Name/), 'Bob')
    await user.clear(idInput)
    await user.type(idInput, 'custom_id')
    await user.type(screen.getByLabelText(/Display Name/), ' Smith')
    expect(idInput).toHaveValue('custom_id')
  })
})

describe('PersonFormModal create submission', () => {
  it('submits PersonCreate with parsed comma-separated fields and contact info', async () => {
    const user = userEvent.setup()
    render(<PersonFormModal {...baseProps} />)
    await user.type(screen.getByLabelText(/Display Name/), 'Bob Smith')
    await user.type(screen.getByLabelText(/Aliases/), 'bobby , b.smith , ')
    await user.type(screen.getByLabelText(/Tags/), 'work, eng')
    await user.type(screen.getByLabelText('Email'), 'bob@x.com')
    await user.type(screen.getByLabelText('GitHub'), '@bsmith')
    await user.type(screen.getByLabelText(/Add a note/), 'met at conf')
    await user.click(screen.getByRole('button', { name: 'Add Person' }))

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
    await user.type(screen.getByLabelText(/Display Name/), 'Bob')
    await user.click(screen.getByRole('button', { name: 'Add Person' }))
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
        title="Edit Person"
        submitLabel="Save Changes"
        isEdit
        initialData={makePerson()}
      />,
    )
    expect(screen.getByLabelText(/Display Name/)).toHaveValue('Alice Chen')
    const idInput = screen.getByLabelText(/Identifier/)
    expect(idInput).toHaveValue('alice_chen')
    expect(idInput).toBeDisabled()
    expect(screen.getByLabelText(/Aliases/)).toHaveValue('ali')
    expect(screen.getByLabelText(/Tags/)).toHaveValue('work')
    expect(screen.getByLabelText('Email')).toHaveValue('a@x.com')
  })

  it('submits PersonUpdate with replace_aliases true', async () => {
    const user = userEvent.setup()
    render(
      <PersonFormModal
        {...baseProps}
        title="Edit Person"
        submitLabel="Save Changes"
        isEdit
        initialData={makePerson()}
      />,
    )
    await user.click(screen.getByRole('button', { name: 'Save Changes' }))
    expect(baseProps.onSubmit).toHaveBeenCalledWith({
      display_name: 'Alice Chen',
      aliases: ['ali'],
      tags: ['work'],
      content: undefined,
      contact_info: { email: 'a@x.com', phone: '555' },
      replace_aliases: true,
    })
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
    await user.clear(screen.getByLabelText(/Display Name/))
    await user.type(screen.getByLabelText(/Display Name/), 'New Name')
    expect(screen.getByLabelText(/Identifier/)).toHaveValue('fixed_id')
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
    expect(screen.getByLabelText(/Display Name/)).toHaveFocus()
  })
})

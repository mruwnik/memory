import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const listPeople = vi.fn()

vi.mock('@/hooks/usePeople', () => ({
  usePeople: () => ({ listPeople }),
}))

import { TaskPeopleField } from './TaskPeopleField'

const me = { id: 5, display_name: 'Test User' }
const ann = { id: 8, display_name: 'Ann Lee' }

const renderField = (props: Partial<Parameters<typeof TaskPeopleField>[0]> = {}) => {
  const onChange = vi.fn()
  render(<TaskPeopleField createTasks people={[]} me={me} onChange={onChange} {...props} />)
  return { onChange, user: userEvent.setup() }
}

beforeEach(() => {
  vi.clearAllMocks()
  listPeople.mockResolvedValue([])
})

describe('TaskPeopleField', () => {
  it('turns tasks off, keeping the picked people', async () => {
    const { onChange, user } = renderField({ people: [ann] })
    await user.click(screen.getByRole('checkbox', { name: 'Create tasks from action items' }))
    expect(onChange).toHaveBeenCalledWith(false, [ann])
  })

  it('hides the people picker while tasks are off', () => {
    renderField({ createTasks: false })
    expect(screen.queryByLabelText('Only for these people')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Only me/ })).not.toBeInTheDocument()
  })

  it('picks only the owner with one click', async () => {
    const { onChange, user } = renderField({ people: [ann] })
    await user.click(screen.getByRole('button', { name: 'Only me (Test User)' }))
    expect(onChange).toHaveBeenCalledWith(true, [me])
  })

  it.each([
    ['the owner is already the only pick', [me], me],
    ['there is no owner Person', [], null],
  ])('offers no "Only me" when %s', (_, people, owner) => {
    renderField({ people, me: owner })
    expect(screen.queryByRole('button', { name: /Only me/ })).not.toBeInTheDocument()
  })

  it('searches people on the server and adds the one picked', async () => {
    listPeople.mockImplementation(async ({ search }: { search?: string }) =>
      search === 'ann' ? [{ ...ann, identifier: 'ann', aliases: [], contact_info: {}, tags: [], created_at: null }] : [])
    const { onChange, user } = renderField()

    await user.type(screen.getByLabelText('Only for these people'), 'ann')
    await user.click(await screen.findByRole('option', { name: 'Ann Lee' }))

    expect(listPeople).toHaveBeenCalledWith({ search: 'ann', limit: 20 })
    expect(onChange).toHaveBeenCalledWith(true, [ann])
  })

  it('shows picked people by name', () => {
    renderField({ people: [ann] })
    expect(screen.getByRole('button', { name: 'Remove Ann Lee' })).toBeInTheDocument()
  })

  it('still offers the owner when people search fails', async () => {
    listPeople.mockRejectedValue(new Error('missing scope'))
    const { user } = renderField()
    await user.click(screen.getByLabelText('Only for these people'))
    await waitFor(() => expect(listPeople).toHaveBeenCalled())
    expect(screen.getByRole('option', { name: /Test User/ })).toBeInTheDocument()
  })
})

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { render, screen, waitFor, userEvent } from '@/test/utils'
import UserSelector, {
  useUserSelection,
  ALL_USERS_ID,
  NO_USER_ID,
  type SelectedUser,
} from './UserSelector'

const authState = {
  hasScope: vi.fn((s: string) => s === '*'),
  user: { id: 1, name: 'Admin', email: 'a@x', user_type: 'human', scopes: ['*'] },
}
const listUsers = vi.fn()

vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => authState,
}))
vi.mock('../../hooks/useUsers', () => ({
  useUsers: () => ({ listUsers }),
}))

const sampleUsers = [
  { id: 1, name: 'Admin', user_type: 'human' },
  { id: 2, name: 'Bob', user_type: 'human' },
  { id: 3, name: 'Bot', user_type: 'bot' },
]

beforeEach(() => {
  localStorage.clear()
  authState.hasScope = vi.fn((s: string) => s === '*')
  authState.user = {
    id: 1,
    name: 'Admin',
    email: 'a@x',
    user_type: 'human',
    scopes: ['*'],
  }
  listUsers.mockReset()
  listUsers.mockResolvedValue(sampleUsers)
})

const selected: SelectedUser = { type: 'user', id: 1, name: 'Admin' }

describe('UserSelector rendering', () => {
  it('renders nothing for non-admins', () => {
    authState.hasScope = vi.fn(() => false)
    const { container } = render(
      <UserSelector value={selected} onChange={() => {}} />,
    )
    expect(container).toBeEmptyDOMElement()
    expect(listUsers).not.toHaveBeenCalled()
  })

  it('shows a loading state before users resolve', () => {
    listUsers.mockReturnValue(new Promise(() => {}))
    render(<UserSelector value={selected} onChange={() => {}} />)
    expect(screen.getByText('Loading users...')).toBeInTheDocument()
  })

  it('renders the loaded users with a (you) marker for the current user', async () => {
    render(<UserSelector value={selected} onChange={() => {}} />)
    await waitFor(() =>
      expect(screen.getByLabelText('View as:')).toBeInTheDocument(),
    )
    const options = screen.getAllByRole('option') as HTMLOptionElement[]
    expect(options.some(o => o.textContent?.includes('Admin (you)'))).toBe(true)
    expect(options.some(o => o.textContent?.includes('Bob'))).toBe(true)
  })

  it('shows an error message when loading fails', async () => {
    listUsers.mockRejectedValue(new Error('boom'))
    render(<UserSelector value={selected} onChange={() => {}} />)
    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument())
  })

  it('filters out bot users when onlyHumanUsers is set', async () => {
    render(
      <UserSelector value={selected} onChange={() => {}} onlyHumanUsers />,
    )
    await waitFor(() =>
      expect(screen.getByLabelText('View as:')).toBeInTheDocument(),
    )
    expect(screen.queryByText(/Bot/)).not.toBeInTheDocument()
  })

  it('restricts to filterToUsers when provided', async () => {
    render(
      <UserSelector
        value={selected}
        onChange={() => {}}
        filterToUsers={[{ id: 2, name: 'Bob' }]}
      />,
    )
    await waitFor(() =>
      expect(screen.getByLabelText('View as:')).toBeInTheDocument(),
    )
    const options = screen.getAllByRole('option') as HTMLOptionElement[]
    expect(options).toHaveLength(1)
    expect(options[0].textContent).toContain('Bob')
  })

  it('renders All users and System options when requested', async () => {
    render(
      <UserSelector
        value={selected}
        onChange={() => {}}
        showAllOption
        showNoneOption
      />,
    )
    await waitFor(() =>
      expect(screen.getByLabelText('View as:')).toBeInTheDocument(),
    )
    expect(screen.getByText('All users')).toBeInTheDocument()
    expect(screen.getByText('System (no user)')).toBeInTheDocument()
  })
})

describe('UserSelector onChange', () => {
  it('emits a concrete user selection', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<UserSelector value={selected} onChange={onChange} />)
    await waitFor(() =>
      expect(screen.getByLabelText('View as:')).toBeInTheDocument(),
    )
    await user.selectOptions(screen.getByLabelText('View as:'), '2')
    expect(onChange).toHaveBeenCalledWith({ type: 'user', id: 2, name: 'Bob' })
  })

  it('emits the All users sentinel', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<UserSelector value={selected} onChange={onChange} showAllOption />)
    await waitFor(() =>
      expect(screen.getByLabelText('View as:')).toBeInTheDocument(),
    )
    await user.selectOptions(screen.getByLabelText('View as:'), String(ALL_USERS_ID))
    expect(onChange).toHaveBeenCalledWith({
      type: 'user',
      id: ALL_USERS_ID,
      name: 'All users',
    })
  })

  it('emits the No user sentinel', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()
    render(<UserSelector value={selected} onChange={onChange} showNoneOption />)
    await waitFor(() =>
      expect(screen.getByLabelText('View as:')).toBeInTheDocument(),
    )
    await user.selectOptions(screen.getByLabelText('View as:'), String(NO_USER_ID))
    expect(onChange).toHaveBeenCalledWith({
      type: 'user',
      id: NO_USER_ID,
      name: 'No user',
    })
  })
})

describe('sentinel constants', () => {
  it('uses -1 for all users and -2 for no user', () => {
    expect(ALL_USERS_ID).toBe(-1)
    expect(NO_USER_ID).toBe(-2)
  })
})

describe('useUserSelection', () => {
  it('defaults to the current user for a fresh admin', () => {
    const { result } = renderHook(() => useUserSelection())
    expect(result.current[0]).toEqual({ type: 'user', id: 1, name: 'Admin' })
  })

  it('defaults to All users when defaultToAll is set for an admin', () => {
    const { result } = renderHook(() => useUserSelection('k', true))
    expect(result.current[0]).toEqual({
      type: 'user',
      id: ALL_USERS_ID,
      name: 'All users',
    })
  })

  it('restores a stored selection for admins', () => {
    localStorage.setItem(
      'adminSelectedUser',
      JSON.stringify({ type: 'user', id: 2, name: 'Bob' }),
    )
    const { result } = renderHook(() => useUserSelection())
    expect(result.current[0]).toEqual({ type: 'user', id: 2, name: 'Bob' })
  })

  it('ignores malformed stored JSON and falls back to current user', () => {
    localStorage.setItem('adminSelectedUser', '{not json')
    const { result } = renderHook(() => useUserSelection())
    expect(result.current[0].id).toBe(1)
  })

  it('persists a new selection to localStorage for admins', () => {
    const { result } = renderHook(() => useUserSelection())
    act(() => {
      result.current[1]({ type: 'user', id: 2, name: 'Bob' })
    })
    expect(result.current[0]).toEqual({ type: 'user', id: 2, name: 'Bob' })
    expect(JSON.parse(localStorage.getItem('adminSelectedUser')!)).toEqual({
      type: 'user',
      id: 2,
      name: 'Bob',
    })
  })

  it('does not persist for non-admins', () => {
    authState.hasScope = vi.fn(() => false)
    const { result } = renderHook(() => useUserSelection())
    act(() => {
      result.current[1]({ type: 'user', id: 9, name: 'X' })
    })
    expect(localStorage.getItem('adminSelectedUser')).toBeNull()
  })

  it('starts non-admins at the current user', () => {
    authState.hasScope = vi.fn(() => false)
    const { result } = renderHook(() => useUserSelection())
    expect(result.current[0]).toEqual({ type: 'user', id: 1, name: 'Admin' })
  })
})

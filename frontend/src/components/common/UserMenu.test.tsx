import { describe, it, expect, vi } from 'vitest'
import { renderWithRouter, screen, waitFor } from '@/test/utils'
import type { AuthUser } from '../../hooks/useAuth'
import UserMenu from './UserMenu'

const makeUser = (overrides: Partial<AuthUser> = {}): AuthUser => ({
  id: 1,
  name: 'Ada Lovelace',
  email: 'ada@example.com',
  user_type: 'human',
  scopes: ['*'],
  ...overrides,
})

const renderMenu = (props: Partial<React.ComponentProps<typeof UserMenu>> = {}) =>
  renderWithRouter(
    <UserMenu
      user={props.user ?? makeUser()}
      onLogout={props.onLogout ?? (() => {})}
      hasScope={props.hasScope ?? (() => true)}
    />,
  )

describe('UserMenu', () => {
  it('shows the user name and computed initials', () => {
    renderMenu()
    expect(screen.getAllByText('Ada Lovelace').length).toBeGreaterThan(0)
    expect(screen.getByText('AL')).toBeInTheDocument()
  })

  it('caps initials at two characters', () => {
    renderMenu({ user: makeUser({ name: 'John Quincy Adams Smith' }) })
    expect(screen.getByText('JQ')).toBeInTheDocument()
  })

  it('keeps the menu closed initially', () => {
    renderMenu()
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('opens the menu on trigger click and sets aria-expanded', async () => {
    const { user } = renderMenu()
    const trigger = screen.getByRole('button', { name: 'User menu' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    await user.click(trigger)
    expect(screen.getByRole('menu')).toBeInTheDocument()
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
  })

  it('renders a Settings link pointing at the settings route', async () => {
    const { user } = renderMenu()
    await user.click(screen.getByRole('button', { name: 'User menu' }))
    const link = screen.getByRole('menuitem', { name: /Settings/ })
    expect(link).toHaveAttribute('href', '/ui/settings')
  })

  it('calls onLogout and closes when Logout is clicked', async () => {
    const onLogout = vi.fn()
    const { user } = renderMenu({ onLogout })
    await user.click(screen.getByRole('button', { name: 'User menu' }))
    await user.click(screen.getByRole('menuitem', { name: /Logout/ }))
    expect(onLogout).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  it('closes the menu when clicking outside', async () => {
    const { user } = renderMenu()
    await user.click(screen.getByRole('button', { name: 'User menu' }))
    expect(screen.getByRole('menu')).toBeInTheDocument()
    await user.click(document.body)
    await waitFor(() =>
      expect(screen.queryByRole('menu')).not.toBeInTheDocument(),
    )
  })

  it('closes the menu on Escape', async () => {
    const { user } = renderMenu()
    await user.click(screen.getByRole('button', { name: 'User menu' }))
    await user.keyboard('{Escape}')
    await waitFor(() =>
      expect(screen.queryByRole('menu')).not.toBeInTheDocument(),
    )
  })

  it('cycles focus with ArrowDown across menu items', async () => {
    const { user } = renderMenu()
    await user.click(screen.getByRole('button', { name: 'User menu' }))
    const items = screen.getAllByRole('menuitem')
    // First item gets focus on open.
    await waitFor(() => expect(items[0]).toHaveFocus())
    await user.keyboard('{ArrowDown}')
    expect(items[1]).toHaveFocus()
    // Wraps around back to the first.
    await user.keyboard('{ArrowDown}')
    expect(items[0]).toHaveFocus()
  })

  it('wraps focus to the last item with ArrowUp from the first', async () => {
    const { user } = renderMenu()
    await user.click(screen.getByRole('button', { name: 'User menu' }))
    const items = screen.getAllByRole('menuitem')
    await waitFor(() => expect(items[0]).toHaveFocus())
    await user.keyboard('{ArrowUp}')
    expect(items[items.length - 1]).toHaveFocus()
  })

  it('moves focus to first/last with Home/End', async () => {
    const { user } = renderMenu()
    await user.click(screen.getByRole('button', { name: 'User menu' }))
    const items = screen.getAllByRole('menuitem')
    await user.keyboard('{End}')
    expect(items[items.length - 1]).toHaveFocus()
    await user.keyboard('{Home}')
    expect(items[0]).toHaveFocus()
  })
})

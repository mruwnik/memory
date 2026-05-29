import { describe, it, expect, vi } from 'vitest'
import { renderWithUser, screen } from '@/test/utils'
import LoginPrompt from './LoginPrompt'

describe('LoginPrompt', () => {
  it('renders the app title and prompt text', () => {
    renderWithUser(<LoginPrompt onLogin={() => {}} />)
    expect(screen.getByText('Memory App')).toBeInTheDocument()
    expect(
      screen.getByText('Please log in to access your memory database'),
    ).toBeInTheDocument()
  })

  it('invokes onLogin when the Log In button is clicked', async () => {
    const onLogin = vi.fn()
    const { user } = renderWithUser(<LoginPrompt onLogin={onLogin} />)
    await user.click(screen.getByRole('button', { name: 'Log In' }))
    expect(onLogin).toHaveBeenCalledTimes(1)
  })
})

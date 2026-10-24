import { describe, it, expect, vi } from 'vitest'
import { renderWithUser, screen } from '@/test/utils'
import LoginPrompt from './LoginPrompt'

describe('LoginPrompt', () => {
  it('renders the app title and prompt text', () => {
    renderWithUser(<LoginPrompt onLogin={() => {}} />)
    expect(screen.getByRole('heading', { name: 'Memory' })).toBeInTheDocument()
    expect(screen.getByText('Log in to your knowledge base.')).toBeInTheDocument()
  })

  it('invokes onLogin when the Log In button is clicked', async () => {
    const onLogin = vi.fn()
    const { user } = renderWithUser(<LoginPrompt onLogin={onLogin} />)
    await user.click(screen.getByRole('button', { name: 'Log in' }))
    expect(onLogin).toHaveBeenCalledTimes(1)
  })
})

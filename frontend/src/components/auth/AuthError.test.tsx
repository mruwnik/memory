import { describe, it, expect, vi } from 'vitest'
import { renderWithUser, screen } from '@/test/utils'
import AuthError from './AuthError'

describe('AuthError', () => {
  it('renders the error message and heading', () => {
    renderWithUser(<AuthError error="Token expired" onRetry={() => {}} />)
    expect(screen.getByText('Authentication Error')).toBeInTheDocument()
    expect(screen.getByText('Token expired')).toBeInTheDocument()
  })

  it('renders a Try Again button', () => {
    renderWithUser(<AuthError error="oops" onRetry={() => {}} />)
    expect(
      screen.getByRole('button', { name: 'Try Again' }),
    ).toBeInTheDocument()
  })

  it('invokes onRetry when the button is clicked', async () => {
    const onRetry = vi.fn()
    const { user } = renderWithUser(<AuthError error="oops" onRetry={onRetry} />)
    await user.click(screen.getByRole('button', { name: 'Try Again' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})

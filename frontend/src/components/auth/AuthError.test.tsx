import { describe, it, expect, vi } from 'vitest'
import { renderWithUser, screen } from '@/test/utils'
import AuthError from './AuthError'

describe('AuthError', () => {
  it('renders the error message and heading', () => {
    renderWithUser(<AuthError error="Token expired" onRetry={() => {}} />)
    expect(screen.getByText('Authentication error')).toBeInTheDocument()
    expect(screen.getByText('Token expired')).toBeInTheDocument()
  })

  it('renders a Retry button', () => {
    renderWithUser(<AuthError error="oops" onRetry={() => {}} />)
    expect(
      screen.getByRole('button', { name: 'Retry' }),
    ).toBeInTheDocument()
  })

  it('invokes onRetry when the button is clicked', async () => {
    const onRetry = vi.fn()
    const { user } = renderWithUser(<AuthError error="oops" onRetry={onRetry} />)
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})

import { describe, it, expect, beforeEach } from 'vitest'
import { renderWithRouter, screen, mockFetch, mockResponse } from '@/test/utils'
import ResetPassword from './ResetPassword'

const openLink = (hash: string) => window.history.replaceState(null, '', `/ui/reset-password${hash}`)

describe('ResetPassword', () => {
  beforeEach(() => openLink('#token=v1.abc.def'))

  it('sends the token from the link with the new password', async () => {
    const fetch = mockFetch(async () => mockResponse({ json: { status: 'password_reset' } }))
    const { user } = renderWithRouter(<ResetPassword />)

    await user.type(screen.getByLabelText('New password'), 'newpass123')
    await user.type(screen.getByLabelText('Confirm new password'), 'newpass123')
    await user.click(screen.getByRole('button', { name: 'Set password' }))

    expect(await screen.findByText(/Password changed/)).toBeInTheDocument()
    const [url, init] = fetch.mock.calls[0]
    expect(String(url)).toMatch(/\/auth\/password-reset\/confirm$/)
    expect(JSON.parse(String(init?.body))).toEqual({ token: 'v1.abc.def', new_password: 'newpass123' })
  })

  it('takes the token out of the address bar', () => {
    mockFetch()
    renderWithRouter(<ResetPassword />)
    expect(window.location.hash).toBe('')
  })

  it('refuses mismatched passwords without calling the server', async () => {
    const fetch = mockFetch()
    const { user } = renderWithRouter(<ResetPassword />)

    await user.type(screen.getByLabelText('New password'), 'newpass123')
    await user.type(screen.getByLabelText('Confirm new password'), 'different1')
    await user.click(screen.getByRole('button', { name: 'Set password' }))

    expect(screen.getByText("The passwords don't match.")).toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('offers a new link when the server rejects this one', async () => {
    mockFetch(async () => mockResponse({ status: 400, json: { detail: 'This reset link is invalid or has expired' } }))
    const { user } = renderWithRouter(<ResetPassword />)

    await user.type(screen.getByLabelText('New password'), 'newpass123')
    await user.type(screen.getByLabelText('Confirm new password'), 'newpass123')
    await user.click(screen.getByRole('button', { name: 'Set password' }))

    expect(await screen.findByText('This reset link is invalid or has expired')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Request a new link' })).toHaveAttribute('href', '/ui/forgot-password')
  })

  it('explains a link without a token', () => {
    openLink('')
    renderWithRouter(<ResetPassword />)
    expect(screen.getByText(/This link is incomplete/)).toBeInTheDocument()
    expect(screen.queryByLabelText('New password')).not.toBeInTheDocument()
  })
})

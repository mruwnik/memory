import { describe, it, expect } from 'vitest'
import { renderWithRouter, screen, mockFetch, mockResponse } from '@/test/utils'
import ForgotPassword from './ForgotPassword'

const fetchWith = (available: boolean) =>
  mockFetch(async (input) =>
    String(input).endsWith('/auth/password-reset')
      ? mockResponse({ json: { available } })
      : mockResponse({ json: { status: 'ok' } }),
  )

describe('ForgotPassword', () => {
  it('sends the address and shows the same confirmation whatever the outcome', async () => {
    const fetch = fetchWith(true)
    const { user } = renderWithRouter(<ForgotPassword />)

    await user.type(await screen.findByLabelText('Email'), 'ada@example.com')
    await user.click(screen.getByRole('button', { name: 'Send reset link' }))

    expect(await screen.findByText(/If an account exists for ada@example.com/)).toBeInTheDocument()
    const [url, init] = fetch.mock.calls[1]
    expect(String(url)).toMatch(/\/auth\/password-reset\/request$/)
    expect(JSON.parse(String(init?.body))).toEqual({ email: 'ada@example.com' })
  })

  it('says so when this server has no reset mail', async () => {
    fetchWith(false)
    renderWithRouter(<ForgotPassword />)

    expect(await screen.findByText(/isn't set up on this server/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Email')).not.toBeInTheDocument()
  })

  it('shows the server reason when the request is refused', async () => {
    mockFetch(async (input) =>
      String(input).endsWith('/auth/password-reset')
        ? mockResponse({ json: { available: true } })
        : mockResponse({ status: 429, json: { detail: 'Too many attempts. Please wait and try again.' } }),
    )
    const { user } = renderWithRouter(<ForgotPassword />)

    await user.type(await screen.findByLabelText('Email'), 'ada@example.com')
    await user.click(screen.getByRole('button', { name: 'Send reset link' }))

    expect(await screen.findByText('Too many attempts. Please wait and try again.')).toBeInTheDocument()
  })
})

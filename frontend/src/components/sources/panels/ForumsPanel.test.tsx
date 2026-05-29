import { describe, it, expect, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { renderWithUser, mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import { ForumsPanel } from './ForumsPanel'

const authMe = { json: { user_id: 1, name: 'A', email: 'a@b.c', user_type: 'human', scopes: ['*'] } }

const numberInput = (labelText: string): HTMLInputElement => {
  const label = screen.getByText(labelText, { selector: 'label' })
  return (label.parentElement as HTMLElement).querySelector('input') as HTMLInputElement
}

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('ForumsPanel', () => {
  it('renders the sync settings with their default values', () => {
    mockFetchRoutes({ '/auth/me': authMe, __default: { json: {} } })
    renderWithUser(<ForumsPanel />)
    expect(screen.getByText('Forums (LessWrong)')).toBeInTheDocument()
    expect(numberInput('Days Back')).toHaveValue(30)
    expect(numberInput('Min Karma')).toHaveValue(10)
    expect(numberInput('Posts per request')).toHaveValue(50)
    expect(numberInput('Max Items')).toHaveValue(1000)
  })

  it('posts a sync with the edited settings and shows the success banner', async () => {
    const mock = mockFetchRoutes({ '/forums/sync': { json: { task_id: 'abc', status: 'queued' } }, '/auth/me': authMe, __default: { json: {} } })
    const { user } = renderWithUser(<ForumsPanel />)

    await user.clear(numberInput('Min Karma'))
    await user.type(numberInput('Min Karma'), '25')
    await user.click(screen.getByLabelText('Alignment Forum only'))
    await user.click(screen.getByRole('button', { name: 'Sync LessWrong' }))

    await waitFor(() => expect(screen.getByText(/LessWrong sync started/)).toBeInTheDocument())
    const call = mock.mock.calls.find(([url]) => String(url).includes('/forums/sync'))!
    const body = JSON.parse((call[1] as RequestInit).body as string)
    expect(body).toMatchObject({ min_karma: 25, af: true, limit: 50, max_items: 1000, tags: [] })
    expect(typeof body.since).toBe('string')
  })

  it('shows the server error detail when the sync fails', async () => {
    mockFetchRoutes({ '/forums/sync': { status: 500, json: { detail: 'rate limited' } }, '/auth/me': authMe, __default: { json: {} } })
    const { user } = renderWithUser(<ForumsPanel />)
    await user.click(screen.getByRole('button', { name: 'Sync LessWrong' }))
    await waitFor(() => expect(screen.getByText('rate limited')).toBeInTheDocument())
  })

  it('falls back to defaults when a number field is cleared', async () => {
    const mock = mockFetchRoutes({ '/forums/sync': { json: { task_id: 'x', status: 'queued' } }, '/auth/me': authMe, __default: { json: {} } })
    const { user } = renderWithUser(<ForumsPanel />)
    await user.clear(numberInput('Days Back'))
    await user.click(screen.getByRole('button', { name: 'Sync LessWrong' }))
    await waitFor(() => expect(screen.getByText(/LessWrong sync started/)).toBeInTheDocument())
    // empty parseInt -> NaN -> fallback 30 days; just assert a since date was sent
    const call = mock.mock.calls.find(([url]) => String(url).includes('/forums/sync'))!
    expect(JSON.parse((call[1] as RequestInit).body as string).since).toBeTruthy()
  })
})

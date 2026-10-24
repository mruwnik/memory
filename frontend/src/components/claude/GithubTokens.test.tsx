import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@/test/utils'
import userEvent from '@testing-library/user-event'

const listGithubTokens = vi.fn()
const saveGithubToken = vi.fn()
const deleteGithubToken = vi.fn()

vi.mock('../../hooks/useClaude', () => ({
  useClaude: () => ({ listGithubTokens, saveGithubToken, deleteGithubToken }),
}))

import GithubTokens from './GithubTokens'

const PAT = 'ghp_secret1234'

beforeEach(() => {
  localStorage.clear()
  listGithubTokens.mockReset().mockResolvedValue([])
  saveGithubToken.mockReset().mockResolvedValue({ kind: 'clone', token_hint: '1234', updated_at: null })
  deleteGithubToken.mockReset().mockResolvedValue(undefined)
})

describe('GithubTokens', () => {
  it('clears tokens cached in localStorage by earlier versions', async () => {
    localStorage.setItem('claude_session_github_token', PAT)
    localStorage.setItem('claude_session_github_token_write', PAT)
    render(<GithubTokens />)
    await waitFor(() => expect(listGithubTokens).toHaveBeenCalled())
    expect(localStorage.getItem('claude_session_github_token')).toBeNull()
    expect(localStorage.getItem('claude_session_github_token_write')).toBeNull()
  })

  it('saves a token to the server, keeps nothing in localStorage, and shows only its hint', async () => {
    const user = userEvent.setup()
    render(<GithubTokens />)
    listGithubTokens.mockResolvedValue([{ kind: 'clone', token_hint: '1234', updated_at: null }])
    await user.type(await screen.findByLabelText('GitHub token (optional)'), `${PAT}{Enter}`)

    expect(saveGithubToken).toHaveBeenCalledWith('clone', PAT)
    expect(await screen.findByText('Saved ••••1234')).toBeInTheDocument()
    expect(document.body).not.toHaveTextContent(PAT)
    expect(Object.values({ ...localStorage })).not.toContain(PAT)
  })

  it('removes a saved token after confirming', async () => {
    const user = userEvent.setup()
    listGithubTokens.mockResolvedValue([{ kind: 'write', token_hint: '9876', updated_at: null }])
    render(<GithubTokens />)
    await user.click(await screen.findByRole('button', { name: 'Remove' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Remove' }))

    await waitFor(() => expect(deleteGithubToken).toHaveBeenCalledWith('write'))
  })
})

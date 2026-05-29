import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { renderWithRouter, mockFetch, mockResponse, setAuthCookies, clearCookies } from '@/test/utils'
import { mcpEnvelopeJson } from '@/hooks/mcpEnvelope.testhelper'
import Search from './Search'

const navigateSpy = vi.fn()
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => navigateSpy }
})

const schemas = {
  blog: { schema: { author: { type: 'string', description: 'Author' } }, size: 0 },
}

const installFetch = (opts: { results?: unknown[]; searchFails?: boolean } = {}) =>
  mockFetch(async (input) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url.includes('/auth/me')) {
      return mockResponse({ json: { user_id: 1, name: 'T', email: 't@e.com', user_type: 'human', scopes: ['*'] } })
    }
    if (url.includes('/mcp/meta_get_metadata_schemas')) {
      return mockResponse({ json: mcpEnvelopeJson(schemas) })
    }
    if (url.includes('/mcp/core_search')) {
      if (opts.searchFails) return mockResponse({ status: 500, json: { detail: 'boom' } })
      return mockResponse({ json: mcpEnvelopeJson(...(opts.results ?? [])) })
    }
    return mockResponse({ status: 404, json: { detail: 'nope' } })
  })

const textResult = (filename: string) => ({
  filename,
  content: 'body',
  chunks: [],
  tags: [],
  mime_type: 'text/plain',
  metadata: null,
})

describe('Search', () => {
  beforeEach(() => {
    clearCookies()
    setAuthCookies()
    navigateSpy.mockReset()
  })

  it('renders the header and back button', () => {
    installFetch()
    renderWithRouter(<Search />)
    expect(screen.getByRole('heading', { name: /Search Knowledge Base/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Back to Dashboard/ })).toBeInTheDocument()
  })

  it('navigates to the dashboard when Back is clicked', async () => {
    installFetch()
    const { user } = renderWithRouter(<Search />)
    await user.click(screen.getByRole('button', { name: /Back to Dashboard/ }))
    expect(navigateSpy).toHaveBeenCalledWith('/ui/dashboard')
  })

  it('shows the empty state initially', () => {
    installFetch()
    renderWithRouter(<Search />)
    expect(screen.getByText('No results found')).toBeInTheDocument()
  })

  it('renders results returned from a search', async () => {
    installFetch({ results: [textResult('alpha.txt'), textResult('beta.txt')] })
    const { user } = renderWithRouter(<Search />)
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'query')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(screen.getByText('Found 2 results')).toBeInTheDocument())
    expect(screen.getByRole('heading', { name: 'alpha.txt' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'beta.txt' })).toBeInTheDocument()
  })

  it('uses the singular result wording for one result', async () => {
    installFetch({ results: [textResult('only.txt')] })
    const { user } = renderWithRouter(<Search />)
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'q')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(screen.getByText('Found 1 result')).toBeInTheDocument())
  })

  it('shows the empty state when a search returns nothing', async () => {
    installFetch({ results: [] })
    const { user } = renderWithRouter(<Search />)
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'q')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(screen.getByText('No results found')).toBeInTheDocument())
  })

  it('clears results and logs on a search error', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    installFetch({ searchFails: true })
    const { user } = renderWithRouter(<Search />)
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'q')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(errSpy).toHaveBeenCalledWith('Search error:', expect.anything()))
    expect(screen.getByText('No results found')).toBeInTheDocument()
    errSpy.mockRestore()
  })

  it('does not search when the query is only whitespace', async () => {
    const fetchMock = installFetch({ results: [textResult('x.txt')] })
    const { user } = renderWithRouter(<Search />)
    // type spaces; required attribute lets a non-empty (space) value submit,
    // but handleSearch early-returns on a trimmed-empty query.
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), '   ')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    const searchCalls = fetchMock.mock.calls.filter(([u]) =>
      String(u).includes('/mcp/core_search'),
    )
    expect(searchCalls).toHaveLength(0)
  })
})

import { describe, it, expect, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { renderWithRouter, mcpToolFromRequest, mockFetch, mockResponse, setAuthCookies, clearCookies } from '@/test/utils'
import { mcpEnvelopeJson } from '@/hooks/mcpEnvelope.testhelper'
import { useLocation } from 'react-router-dom'
import Search from './Search'

const LocationProbe = () => <div data-testid="location">{useLocation().search}</div>

const schemas = {
  blog: {
    schema: { author: { type: 'str', description: 'Author' } },
    filters: { author: { type: 'str', description: 'Author' } },
    size: 1,
  },
}
const searchDefaults = { use_bm25: true, use_hyde: false, use_reranking: true, use_query_analysis: false }

const installFetch = (opts: { results?: unknown[]; searchFails?: boolean; schemasFail?: boolean } = {}) =>
  mockFetch(async (input, init) => {
    const url = typeof input === 'string' ? input : input.toString()
    const tool = mcpToolFromRequest(input, init)
    if (url.includes('/auth/me')) {
      return mockResponse({ json: { user_id: 1, name: 'T', email: 't@e.com', user_type: 'human', scopes: ['*'] } })
    }
    if (tool === 'meta_get_metadata_schemas') {
      if (opts.schemasFail) return mockResponse({ status: 500, json: { detail: 'schemas down' } })
      return mockResponse({ json: mcpEnvelopeJson(schemas) })
    }
    if (tool === 'meta_get_tags') {
      return mockResponse({ json: mcpEnvelopeJson(['work', 'home']) })
    }
    if (tool === 'meta_get_search_defaults') {
      return mockResponse({ json: mcpEnvelopeJson(searchDefaults) })
    }
    if (tool === 'core_search') {
      if (opts.searchFails) return mockResponse({ status: 500, json: { detail: 'boom' } })
      return mockResponse({ json: mcpEnvelopeJson(opts.results ?? []) })
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
  })

  it('renders the header', () => {
    installFetch()
    renderWithRouter(<Search />)
    expect(screen.getByRole('heading', { name: 'Search' })).toBeInTheDocument()
  })

  it('shows no empty state before the first search', () => {
    installFetch()
    renderWithRouter(<Search />)
    expect(screen.queryByText('No results found')).not.toBeInTheDocument()
  })

  it('renders results returned from a search', async () => {
    installFetch({ results: [textResult('alpha.txt'), textResult('beta.txt')] })
    const { user } = renderWithRouter(<Search />)
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'query')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Found 2 results'))
    expect(screen.getByText('alpha.txt')).toBeInTheDocument()
    expect(screen.getByText('beta.txt')).toBeInTheDocument()
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

  it('shows the failure instead of an empty result on a search error', async () => {
    installFetch({ searchFails: true })
    const { user } = renderWithRouter(<Search />)
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'q')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
    expect(screen.queryByText('No results found')).not.toBeInTheDocument()
  })

  it('does not search when the query is only whitespace', async () => {
    const fetchMock = installFetch({ results: [textResult('x.txt')] })
    const { user } = renderWithRouter(<Search />)
    // type spaces; required attribute lets a non-empty (space) value submit,
    // but handleSearch early-returns on a trimmed-empty query.
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), '   ')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    const searchCalls = fetchMock.mock.calls.filter(
      ([u, init]) => mcpToolFromRequest(u, init) === 'core_search',
    )
    expect(searchCalls).toHaveLength(0)
  })

  it('runs the search in the URL on load and puts a new query in the URL', async () => {
    const fetchMock = installFetch({ results: [textResult('alpha.txt')] })
    const { user } = renderWithRouter(<><Search /><LocationProbe /></>, { initialEntries: ['/ui/search?q=alpha'] })
    expect(await screen.findByText('alpha.txt')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Search your knowledge base...')).toHaveValue('alpha')
    const query = screen.getByPlaceholderText('Search your knowledge base...')
    await user.clear(query)
    await user.type(query, 'beta')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('?q=beta'))
    const searched = fetchMock.mock.calls.filter(([u, init]) => mcpToolFromRequest(u, init) === 'core_search')
    expect(searched).toHaveLength(2)
  })

  it('says when the search filters fail to load', async () => {
    installFetch({ schemasFail: true })
    renderWithRouter(<Search />)
    expect(await screen.findByText(/Couldn't load search filters/)).toBeInTheDocument()
  })
})

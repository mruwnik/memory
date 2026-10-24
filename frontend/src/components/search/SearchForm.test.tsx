import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor, fireEvent } from '@testing-library/react'
import { renderWithUser, mcpToolFromRequest, mockFetch, mockResponse, setAuthCookies, clearCookies } from '@/test/utils'
import { mcpEnvelopeJson } from '@/hooks/mcpEnvelope.testhelper'
import SearchForm from './SearchForm'

const schemas = {
  blog: {
    schema: { author: { type: 'str', description: 'Author' } },
    filters: { author: { type: 'str', description: 'Author' } },
    size: 1,
  },
}
const searchDefaults = { use_bm25: true, use_hyde: false, use_reranking: true, use_query_analysis: false }

// Route fetch: /auth/me for useAuth, MCP calls by tool name in the JSON-RPC body.
const installFetch = (searchResults: unknown[] = []) =>
  mockFetch(async (input, init) => {
    const url = typeof input === 'string' ? input : input.toString()
    const tool = mcpToolFromRequest(input, init)
    if (url.includes('/auth/me')) {
      return mockResponse({ json: { user_id: 1, name: 'T', email: 't@e.com', user_type: 'human', scopes: ['*'] } })
    }
    if (tool === 'meta_get_metadata_schemas') {
      return mockResponse({ json: mcpEnvelopeJson(schemas) })
    }
    if (tool === 'meta_get_tags') {
      return mockResponse({ json: mcpEnvelopeJson(['work', 'home']) })
    }
    if (tool === 'meta_get_search_defaults') {
      return mockResponse({ json: mcpEnvelopeJson(searchDefaults) })
    }
    if (tool === 'core_search') {
      return mockResponse({ json: mcpEnvelopeJson(...searchResults) })
    }
    return mockResponse({ status: 404, json: { detail: 'nope' } })
  })

describe('SearchForm', () => {
  beforeEach(() => {
    clearCookies()
    setAuthCookies()
  })

  it('renders the query input and search button', () => {
    installFetch()
    renderWithUser(<SearchForm isLoading={false} onSearch={() => {}} />)
    expect(screen.getByPlaceholderText('Search your knowledge base...')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Search' })).toBeInTheDocument()
  })

  it('shows Searching... and disables the button while loading', () => {
    installFetch()
    renderWithUser(<SearchForm isLoading={true} onSearch={() => {}} />)
    const btn = screen.getByRole('button', { name: 'Searching...' })
    expect(btn).toBeDisabled()
  })

  it('loads metadata schemas into modality options on mount', async () => {
    installFetch()
    renderWithUser(<SearchForm isLoading={false} onSearch={() => {}} />)
    // Modalities section starts with all schema keys selected (createFlags default true)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Remove blog' })).toBeInTheDocument())
  })

  it('submits search params with the typed query and defaults', async () => {
    installFetch()
    const onSearch = vi.fn()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={onSearch} />)
    await waitFor(() => expect(screen.getByLabelText('BM25 keyword search')).toBeEnabled())
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'hello')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    expect(onSearch).toHaveBeenCalledTimes(1)
    const params = onSearch.mock.calls[0][0]
    expect(params.query).toBe('hello')
    expect(params.config).toEqual({
      previews: false,
      useScores: false,
      limit: 10,
      enhancements: searchDefaults,
    })
    expect(params.filters.tags).toEqual([])
  })

  it('includes selected modalities in submitted params', async () => {
    installFetch()
    const onSearch = vi.fn()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={onSearch} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Remove blog' })).toBeInTheDocument())
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'q')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    expect(onSearch.mock.calls[0][0].modalities).toEqual(['blog'])
  })

  it('drops a modality removed from the picker', async () => {
    installFetch()
    const onSearch = vi.fn()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={onSearch} />)
    await user.click(await screen.findByRole('button', { name: 'Remove blog' }))
    await user.type(screen.getByRole('textbox', { name: 'Search query' }), 'q')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    expect(onSearch.mock.calls[0][0].modalities).toEqual([])
  })

  it('reflects the previews checkbox in config', async () => {
    installFetch()
    const onSearch = vi.fn()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={onSearch} />)
    await user.click(screen.getByLabelText('Include content previews'))
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'q')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    expect(onSearch.mock.calls[0][0].config.previews).toBe(true)
  })

  it('reflects the useScores checkbox in config', async () => {
    installFetch()
    const onSearch = vi.fn()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={onSearch} />)
    await user.click(screen.getByLabelText('Score results with an LLM before returning'))
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'q')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    expect(onSearch.mock.calls[0][0].config.useScores).toBe(true)
  })

  it.each([
    [1, false],
    [2, true],
  ])('clicking BM25 %i times submits use_bm25=%s', async (clicks, expected) => {
    installFetch()
    const onSearch = vi.fn()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={onSearch} />)
    const bm25 = screen.getByLabelText('BM25 keyword search')
    await waitFor(() => expect(bm25).toBeEnabled())
    for (let i = 0; i < clicks; i++) await user.click(bm25)
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'q')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    expect(onSearch.mock.calls[0][0].config.enhancements).toEqual({ ...searchDefaults, use_bm25: expected })
  })

  it.each([
    ['BM25 keyword search', true],
    ['HyDE (hypothetical document expansion)', false],
    ['Reranking (cross-encoder)', true],
    ['Query analysis (LLM-based)', false],
  ])('offers %s only when the server allows it (%s)', async (label, available) => {
    installFetch()
    renderWithUser(<SearchForm isLoading={false} onSearch={() => {}} />)
    await waitFor(() => expect(screen.getByLabelText('BM25 keyword search')).toBeEnabled())
    expect((screen.getByLabelText(label) as HTMLInputElement).disabled).toBe(!available)
  })

  it('offers the server tags and submits the chosen ones', async () => {
    installFetch()
    const onSearch = vi.fn()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={onSearch} />)
    await user.click(await screen.findByPlaceholderText('Any tag'))
    await user.click(await screen.findByRole('option', { name: 'work' }))
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'q')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    expect(onSearch.mock.calls[0][0].filters.tags).toEqual(['work'])
  })

  it('updates Max Results limit', async () => {
    installFetch()
    const onSearch = vi.fn()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={onSearch} />)
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'q')
    const limit = screen.getByRole('spinbutton') as HTMLInputElement
    fireEvent.change(limit, { target: { value: '25' } })
    expect(limit).toHaveValue(25)
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(onSearch).toHaveBeenCalled())
    expect(onSearch.mock.calls[0][0].config.limit).toBe(25)
  })

  it.each([
    ['1025', 100],
    ['{Backspace}', 10],
  ])('clamps a limit typed as %j to %s when the search runs', async (typed, sent) => {
    installFetch()
    const onSearch = vi.fn()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={onSearch} />)
    await user.clear(screen.getByRole('spinbutton'))
    await user.type(screen.getByRole('spinbutton'), typed)
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'q')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(onSearch).toHaveBeenCalled())
    expect(onSearch.mock.calls[0][0].config.limit).toBe(sent)
  })

  it('lets a cleared limit be retyped without snapping to a default', async () => {
    installFetch()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={vi.fn()} />)
    const limit = screen.getByRole('spinbutton')
    await user.clear(limit)
    await user.type(limit, '50')
    expect(limit).toHaveValue(50)
  })

  it('includes dynamic filters once a modality field is filled', async () => {
    installFetch()
    const onSearch = vi.fn()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={onSearch} />)
    await waitFor(() => expect(screen.getByText('Blog specific')).toBeInTheDocument())
    await user.type(screen.getByPlaceholderText('Author'), 'Tolkien')
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'q')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    expect(onSearch.mock.calls[0][0].filters.author).toBe('Tolkien')
  })

  it('does not submit when the required query is empty', async () => {
    installFetch()
    const onSearch = vi.fn()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={onSearch} />)
    await user.click(screen.getByRole('button', { name: 'Search' }))
    // HTML5 required prevents submit
    expect(onSearch).not.toHaveBeenCalled()
  })

  it('says when the filters fail to load and still renders the form', async () => {
    mockFetch(async (input) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url.includes('/auth/me')) {
        return mockResponse({ json: { user_id: 1, name: 'T', email: 't@e.com', user_type: 'human', scopes: ['*'] } })
      }
      return mockResponse({ status: 500, json: { detail: 'boom' } })
    })
    renderWithUser(<SearchForm isLoading={false} onSearch={() => {}} />)
    expect(await screen.findByText(/Couldn't load search filters/)).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Search your knowledge base...')).toBeInTheDocument()
  })
})

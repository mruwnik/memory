import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor, fireEvent } from '@testing-library/react'
import { renderWithUser, mockFetch, mockResponse, setAuthCookies, clearCookies } from '@/test/utils'
import SearchForm from './SearchForm'

// Build an MCP JSON-RPC envelope: useMCP reads response.text(), JSON.parses it,
// then maps result.content[].text through JSON.parse.
const mcpEnvelope = (payloads: unknown[]) => ({
  jsonrpc: '2.0',
  id: 1,
  result: { content: payloads.map((p) => ({ type: 'text', text: JSON.stringify(p) })) },
})

const schemas = {
  blog: { schema: { author: { type: 'string', description: 'Author' } }, size: 0 },
}

// Route fetch: /auth/me for useAuth, /mcp/<method> for MCP calls.
const installFetch = (searchResults: unknown[] = []) =>
  mockFetch(async (input) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url.includes('/auth/me')) {
      return mockResponse({ json: { user_id: 1, name: 'T', email: 't@e.com', user_type: 'human', scopes: ['*'] } })
    }
    if (url.includes('/mcp/meta_get_metadata_schemas')) {
      return mockResponse({ json: mcpEnvelope([schemas]) })
    }
    if (url.includes('/mcp/core_search')) {
      return mockResponse({ json: mcpEnvelope(searchResults) })
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
    await waitFor(() => expect(screen.getByText('Modalities (1 selected)')).toBeInTheDocument())
  })

  it('submits search params with the typed query and defaults', async () => {
    installFetch()
    const onSearch = vi.fn()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={onSearch} />)
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'hello')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    expect(onSearch).toHaveBeenCalledTimes(1)
    const params = onSearch.mock.calls[0][0]
    expect(params.query).toBe('hello')
    expect(params.config).toMatchObject({
      previews: false,
      useScores: false,
      limit: 10,
      useBm25: true,
      useHyde: true,
      useReranking: true,
      useQueryAnalysis: true,
    })
    expect(params.filters.tags).toEqual([])
  })

  it('includes selected modalities in submitted params', async () => {
    installFetch()
    const onSearch = vi.fn()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={onSearch} />)
    await waitFor(() => expect(screen.getByText('Modalities (1 selected)')).toBeInTheDocument())
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'q')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    expect(onSearch.mock.calls[0][0].modalities).toEqual(['blog'])
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
    await user.click(screen.getByLabelText('Score results with a LLM before returning'))
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'q')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    expect(onSearch.mock.calls[0][0].config.useScores).toBe(true)
  })

  it('toggles a search enhancement flag (BM25) off', async () => {
    installFetch()
    const onSearch = vi.fn()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={onSearch} />)
    await user.click(screen.getByLabelText('BM25 keyword search'))
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'q')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    expect(onSearch.mock.calls[0][0].config.useBm25).toBe(false)
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

  it('clamps an over-max limit to 100 so it cannot block submission', async () => {
    installFetch()
    const onSearch = vi.fn()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={onSearch} />)
    const limit = screen.getByRole('spinbutton') as HTMLInputElement
    fireEvent.change(limit, { target: { value: '1025' } })
    expect(limit).toHaveValue(100)
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'q')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(onSearch).toHaveBeenCalled())
    expect(onSearch.mock.calls[0][0].config.limit).toBe(100)
  })

  it('clamps the limit to 1 when cleared to an empty/invalid value', async () => {
    installFetch()
    const onSearch = vi.fn()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={onSearch} />)
    const limit = screen.getByRole('spinbutton')
    fireEvent.change(limit, { target: { value: '' } })
    await user.type(screen.getByPlaceholderText('Search your knowledge base...'), 'q')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(onSearch).toHaveBeenCalled())
    expect(onSearch.mock.calls[0][0].config.limit).toBe(1)
  })

  it('includes dynamic filters once a modality field is filled', async () => {
    installFetch()
    const onSearch = vi.fn()
    const { user } = renderWithUser(<SearchForm isLoading={false} onSearch={onSearch} />)
    await waitFor(() => expect(screen.getByText('Blog Specific')).toBeInTheDocument())
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

  it('logs and recovers when schema loading fails', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockFetch(async (input) => {
      const url = typeof input === 'string' ? input : input.toString()
      if (url.includes('/auth/me')) {
        return mockResponse({ json: { user_id: 1, name: 'T', email: 't@e.com', user_type: 'human', scopes: ['*'] } })
      }
      return mockResponse({ status: 500, json: { detail: 'boom' } })
    })
    renderWithUser(<SearchForm isLoading={false} onSearch={() => {}} />)
    await waitFor(() => expect(errSpy).toHaveBeenCalledWith('Failed to load search filters:', expect.anything()))
    // form still renders despite the failure
    expect(screen.getByPlaceholderText('Search your knowledge base...')).toBeInTheDocument()
    errSpy.mockRestore()
  })
})

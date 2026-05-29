import { describe, it, expect, beforeEach } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { renderWithUser, mockFetchRoutes, setAuthCookies, clearCookies } from '@/test/utils'
import { mcpResult, mcpEnvelopeJson } from '@/hooks/mcpEnvelope.testhelper'
import { BooksPanel } from './BooksPanel'

const authMe = { json: { user_id: 1, name: 'A', email: 'a@b.c', user_type: 'human', scopes: ['*'] } }

const book = {
  id: 3,
  title: 'Dune',
  author: 'Frank Herbert',
  publisher: 'Chilton',
  published: null,
  language: 'en',
  total_pages: 412,
  tags: ['scifi'],
  section_count: 24,
  file_path: 'books/dune.epub',
}

beforeEach(() => {
  clearCookies()
  setAuthCookies()
})

describe('BooksPanel load states', () => {
  it('shows the empty state when no books are indexed', async () => {
    mockFetchRoutes({ '/mcp/books_list_books': mcpResult([]), '/auth/me': authMe, __default: { json: {} } })
    renderWithUser(<BooksPanel />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('No books indexed yet')).toBeInTheDocument())
  })

  it('renders a book with its metadata and a download link', async () => {
    mockFetchRoutes({ '/mcp/books_list_books': mcpResult([book]), '/auth/me': authMe, __default: { json: {} } })
    renderWithUser(<BooksPanel />)
    await waitFor(() => expect(screen.getByText('Dune')).toBeInTheDocument())
    expect(screen.getByText('by Frank Herbert')).toBeInTheDocument()
    expect(screen.getByText('Publisher: Chilton')).toBeInTheDocument()
    expect(screen.getByText('412 pages')).toBeInTheDocument()
    expect(screen.getByText('24 sections')).toBeInTheDocument()
    expect(screen.getByText('scifi')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Dune' })).toHaveAttribute(
      'href',
      '/files/books/dune.epub?download=true',
    )
    expect(screen.getByText('1 books')).toBeInTheDocument()
  })

  it('renders a plain title (no link) when file_path is missing', async () => {
    mockFetchRoutes({ '/mcp/books_list_books': mcpResult([{ ...book, file_path: null }]), '/auth/me': authMe, __default: { json: {} } })
    renderWithUser(<BooksPanel />)
    await waitFor(() => expect(screen.getByText('Dune')).toBeInTheDocument())
    expect(screen.queryByRole('link', { name: 'Dune' })).not.toBeInTheDocument()
  })

  it('shows an error state with retry when the list fetch fails', async () => {
    mockFetchRoutes({ '/mcp/books_list_books': { status: 500, json: {} }, '/auth/me': authMe, __default: { json: {} } })
    renderWithUser(<BooksPanel />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument())
  })
})

describe('BooksPanel upload flow', () => {
  it('uploads a selected file and shows the success banner', async () => {
    const mock = mockFetchRoutes({
      '/mcp/books_list_books': mcpResult([]),
      '/books/upload': { json: { task_id: 't1', status: 'queued' } },
      '/auth/me': authMe,
      __default: { json: {} },
    })
    const { user } = renderWithUser(<BooksPanel />)
    await waitFor(() => expect(screen.getByText('No books indexed yet')).toBeInTheDocument())

    const file = new File(['data'], 'mybook.epub', { type: 'application/epub+zip' })
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(input, file)

    await waitFor(() => expect(screen.getByText('1 book(s) uploaded and queued for processing')).toBeInTheDocument())
    const upload = mock.mock.calls.find(
      ([url, init]) => String(url).includes('/books/upload') && (init as RequestInit)?.method === 'POST',
    )!
    expect((upload[1] as RequestInit).body).toBeInstanceOf(FormData)
  })

  it('shows the error banner when the upload fails', async () => {
    const mock = mockFetchRoutes({ '/mcp/books_list_books': mcpResult([]), '/auth/me': authMe, __default: { json: {} } })
    mock.mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/books/upload')) {
        return { ok: false, status: 413, json: async () => ({ detail: 'file too large' }) } as unknown as Response
      }
      return {
        ok: true,
        status: 200,
        headers: new Headers(),
        json: async () => mcpEnvelopeJson([]),
        text: async () => JSON.stringify(mcpEnvelopeJson([])),
      } as unknown as Response
    })
    const { user } = renderWithUser(<BooksPanel />)
    await waitFor(() => expect(screen.getByText('No books indexed yet')).toBeInTheDocument())
    const file = new File(['data'], 'big.pdf', { type: 'application/pdf' })
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(fileInput, file)
    await waitFor(() => expect(screen.getByText('file too large')).toBeInTheDocument())
  })
})

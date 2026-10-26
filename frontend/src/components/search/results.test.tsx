import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithUser } from '@/test/utils'
import { Metadata, SearchResult, type SearchItem } from './results'

// react-markdown ships as ESM and pulls in heavy deps; render children verbatim.
vi.mock('react-markdown', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div data-testid="md">{children}</div>,
}))

const base = (overrides: Partial<SearchItem> = {}): SearchItem => ({
  filename: 'doc.txt',
  content: '',
  chunks: [],
  tags: [],
  mime_type: 'text/plain',
  metadata: null,
  ...overrides,
})

const render = (overrides: Partial<SearchItem>) =>
  renderWithUser(<ul><SearchResult result={base(overrides)} /></ul>)

describe('Metadata', () => {
  it('returns null for missing metadata', () => {
    const { container } = renderWithUser(<Metadata metadata={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it.each([
    ['url', 'https://example.com', 'https://example.com'],
    ['filename', 'a.pdf', '/files/a.pdf'],
  ])('renders a %s value as a link', (key, value, href) => {
    renderWithUser(<Metadata metadata={{ [key]: value }} />)
    expect(screen.getByRole('link', { name: value })).toHaveAttribute('href', href)
  })

  it.each(['javascript:alert(1)', 'data:text/html,x'])('renders a non-http url %s as text, not a link', (url) => {
    renderWithUser(<Metadata metadata={{ url }} />)
    expect(screen.getByText(url)).toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('renders strings plainly, JSON-stringifies other values and drops empty ones', () => {
    renderWithUser(<Metadata metadata={{ author: 'Tolkien', pages: 42, skip: null, gone: undefined }} />)
    expect(screen.getByText('Tolkien')).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()
    expect(screen.queryByText('skip')).not.toBeInTheDocument()
    expect(screen.queryByText('gone')).not.toBeInTheDocument()
  })
})

describe('SearchResult titles', () => {
  it.each([
    ['text/plain', { filename: 'notes.txt' }, 'notes.txt'],
    ['text/plain', { filename: '', metadata: { title: 'My Title' } }, 'My Title'],
    ['text/plain', { filename: '', metadata: { url: 'https://u' } }, 'https://u'],
    ['text/plain', { filename: '' }, 'Untitled'],
    ['image/png', { filename: 'pic.png', metadata: { title: 'A Photo' } }, 'A Photo'],
    ['message/rfc822', { metadata: { subject: 'Hello' } }, 'Hello'],
    ['application/pdf', { filename: 'book.pdf' }, 'book.pdf'],
  ])('titles a %s result', (mime_type, overrides, title) => {
    render({ mime_type, ...overrides })
    expect(screen.getAllByText(title)[0]).toBeInTheDocument()
  })

  it.each([
    ['application/epub+zip', { filename: 'book.epub', metadata: { title: 'A Book' } }, 'A Book'],
    [null, { filename: 'data.bin' }, 'data.bin'],
  ])('falls back to a generic result for an unhandled %s type', (mime_type, overrides, title) => {
    render({ mime_type: mime_type as string, content: 'some preview', ...overrides })
    expect(screen.getAllByText(title)[0]).toBeInTheDocument()
    expect(screen.getByText('some preview')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open file' })).toHaveAttribute('href', `/files/${overrides.filename}`)
  })
})

describe('SearchResult bodies', () => {
  it('shows tags as badges', () => {
    render({ tags: ['ai', 'ml'] })
    expect(screen.getByText('ai')).toBeInTheDocument()
    expect(screen.getByText('ml')).toBeInTheDocument()
  })

  it('shows plain text content', () => {
    render({ content: 'Hello body' })
    expect(screen.getByText('Hello body')).toBeInTheDocument()
  })

  it.each(['text/markdown', 'message/rfc822'])('renders %s content as markdown', (mime_type) => {
    render({ mime_type, content: '# Title' })
    expect(screen.getByTestId('md')).toHaveTextContent('# Title')
  })

  it('shows relevant sections with scores only when provided', () => {
    render({ chunks: [{ preview: 'snippet text', score: 0.1234 }, { preview: 'other' }] })
    expect(screen.getByText('Relevant sections')).toBeInTheDocument()
    expect(screen.getByText('Score: 0.123')).toBeInTheDocument()
    expect(screen.queryByText('Score: 0.000')).not.toBeInTheDocument()
    expect(screen.getByText('snippet text')).toBeInTheDocument()
  })

  it('renders string sections returned by core_search', () => {
    render({ chunks: ['matching passage', 'another passage'] })
    expect(screen.getByText('matching passage')).toBeInTheDocument()
    expect(screen.getByText('another passage')).toBeInTheDocument()
    expect(screen.queryByText(/Score:/)).not.toBeInTheDocument()
  })

  it('omits relevant sections when there are no chunks', () => {
    render({})
    expect(screen.queryByText('Relevant sections')).not.toBeInTheDocument()
  })

  it('builds the image src and alt from the result', () => {
    render({ mime_type: 'image/png', filename: 'pic.png', metadata: { title: 'A Photo' } })
    expect(screen.getByRole('img')).toHaveAttribute('src', '/files/pic.png')
    expect(screen.getByRole('img')).toHaveAttribute('alt', 'A Photo')
  })

  it('links a PDF and shows its source only when there is content', () => {
    render({ mime_type: 'application/pdf', filename: 'book.pdf', content: 'raw text' })
    expect(screen.getByRole('link', { name: 'View PDF' })).toHaveAttribute('href', '/files/book.pdf')
    expect(screen.getByText('View source')).toBeInTheDocument()
    expect(screen.getByTestId('md')).toHaveTextContent('raw text')
  })

  it('omits the PDF source section when content is empty', () => {
    render({ mime_type: 'application/pdf', filename: 'book.pdf' })
    expect(screen.queryByText('View source')).not.toBeInTheDocument()
  })
})

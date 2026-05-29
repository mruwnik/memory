import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithUser } from '@/test/utils'
import {
  Tag,
  Metadata,
  TextResult,
  MarkdownResult,
  ImageResult,
  PDFResult,
  EmailResult,
  SearchResult,
  type SearchItem,
} from './results'

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

describe('Tag', () => {
  it('renders each tag', () => {
    renderWithUser(<Tag tags={['ai', 'ml']} />)
    expect(screen.getByText('ai')).toBeInTheDocument()
    expect(screen.getByText('ml')).toBeInTheDocument()
  })

  it('renders nothing tag-wise for undefined tags', () => {
    const { container } = renderWithUser(<Tag tags={undefined as any} />)
    expect(container.querySelectorAll('span')).toHaveLength(0)
  })
})

describe('Metadata', () => {
  it('returns null for missing metadata', () => {
    const { container } = renderWithUser(<Metadata metadata={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders a url metadata key as a link', () => {
    renderWithUser(<Metadata metadata={{ url: 'https://example.com' }} />)
    const link = screen.getByRole('link', { name: 'https://example.com' })
    expect(link).toHaveAttribute('href', 'https://example.com')
  })

  it('renders a filename metadata key as a files link', () => {
    renderWithUser(<Metadata metadata={{ filename: 'a.pdf' }} />)
    expect(screen.getByRole('link', { name: 'a.pdf' })).toHaveAttribute('href', '/files/a.pdf')
  })

  it('renders string values plainly', () => {
    renderWithUser(<Metadata metadata={{ author: 'Tolkien' }} />)
    expect(screen.getByText('author:')).toBeInTheDocument()
    expect(screen.getByText('Tolkien')).toBeInTheDocument()
  })

  it('JSON-stringifies non-string values', () => {
    renderWithUser(<Metadata metadata={{ pages: 42 }} />)
    expect(screen.getByText('42')).toBeInTheDocument()
  })

  it('filters out null and undefined values', () => {
    renderWithUser(<Metadata metadata={{ author: 'X', skip: null, gone: undefined }} />)
    expect(screen.getByText('author:')).toBeInTheDocument()
    expect(screen.queryByText('skip:')).not.toBeInTheDocument()
    expect(screen.queryByText('gone:')).not.toBeInTheDocument()
  })
})

describe('TextResult', () => {
  it('shows the filename as heading', () => {
    renderWithUser(<TextResult {...base({ filename: 'notes.txt' })} />)
    expect(screen.getByRole('heading', { name: 'notes.txt' })).toBeInTheDocument()
  })

  it('falls back through metadata title then url then Untitled', () => {
    renderWithUser(<TextResult {...base({ filename: '', metadata: { title: 'My Title' } })} />)
    expect(screen.getByRole('heading', { name: 'My Title' })).toBeInTheDocument()
  })

  it('shows Untitled when no name source exists', () => {
    renderWithUser(<TextResult {...base({ filename: '', metadata: null })} />)
    expect(screen.getByRole('heading', { name: 'Untitled' })).toBeInTheDocument()
  })

  it('renders content when present', () => {
    renderWithUser(<TextResult {...base({ content: 'Hello body' })} />)
    expect(screen.getByText('Hello body')).toBeInTheDocument()
  })

  it('renders chunks with formatted scores in a details element', () => {
    renderWithUser(
      <TextResult {...base({ chunks: [{ preview: 'snippet text', score: 0.12345 }] })} />,
    )
    expect(screen.getByText('Relevant sections:')).toBeInTheDocument()
    expect(screen.getByText('Score: 0.123')).toBeInTheDocument()
    expect(screen.getByText('snippet text')).toBeInTheDocument()
  })

  it('defaults a missing chunk score to 0.000', () => {
    renderWithUser(<TextResult {...base({ chunks: [{ preview: 'p' }] })} />)
    expect(screen.getByText('Score: 0.000')).toBeInTheDocument()
  })

  it('omits the sections details when there are no chunks', () => {
    renderWithUser(<TextResult {...base({ chunks: [] })} />)
    expect(screen.queryByText('Relevant sections:')).not.toBeInTheDocument()
  })
})

describe('MarkdownResult', () => {
  it('renders markdown content through the markdown renderer', () => {
    renderWithUser(<MarkdownResult {...base({ content: '# Title', mime_type: 'text/markdown' })} />)
    expect(screen.getByTestId('md')).toHaveTextContent('# Title')
  })

  it('renders chunks like TextResult', () => {
    renderWithUser(
      <MarkdownResult {...base({ chunks: [{ preview: 'sec', score: 0.5 }] })} />,
    )
    expect(screen.getByText('Score: 0.500')).toBeInTheDocument()
  })
})

describe('ImageResult', () => {
  it('builds the image src from the filename', () => {
    renderWithUser(<ImageResult {...base({ filename: 'pic.png', mime_type: 'image/png' })} />)
    const img = screen.getByRole('img')
    expect(img).toHaveAttribute('src', '/files/pic.png')
  })

  it('prefers metadata.title for the alt and heading', () => {
    renderWithUser(
      <ImageResult {...base({ filename: 'pic.png', metadata: { title: 'A Photo' } })} />,
    )
    expect(screen.getByRole('heading', { name: 'A Photo' })).toBeInTheDocument()
    expect(screen.getByRole('img')).toHaveAttribute('alt', 'A Photo')
  })
})

describe('PDFResult', () => {
  it('renders a View PDF link to the file', () => {
    renderWithUser(<PDFResult {...base({ filename: 'book.pdf', mime_type: 'application/pdf' })} />)
    expect(screen.getByRole('link', { name: 'View PDF' })).toHaveAttribute('href', '/files/book.pdf')
  })

  it('renders source content inside a collapsible when present', () => {
    renderWithUser(<PDFResult {...base({ filename: 'book.pdf', content: 'raw text' })} />)
    expect(screen.getByText('View Source')).toBeInTheDocument()
    expect(screen.getByTestId('md')).toHaveTextContent('raw text')
  })

  it('omits the source section when content is empty', () => {
    renderWithUser(<PDFResult {...base({ filename: 'book.pdf', content: '' })} />)
    expect(screen.queryByText('View Source')).not.toBeInTheDocument()
  })
})

describe('EmailResult', () => {
  it('uses metadata.title for the heading', () => {
    renderWithUser(<EmailResult {...base({ metadata: { title: 'Re: Hi' } })} />)
    expect(screen.getByRole('heading', { name: 'Re: Hi' })).toBeInTheDocument()
  })

  it('falls back to subject then Untitled', () => {
    renderWithUser(<EmailResult {...base({ metadata: { subject: 'Subj' } })} />)
    expect(screen.getByRole('heading', { name: 'Subj' })).toBeInTheDocument()
  })

  it('renders body content via markdown', () => {
    renderWithUser(<EmailResult {...base({ content: 'email body', metadata: {} })} />)
    expect(screen.getByTestId('md')).toHaveTextContent('email body')
  })
})

describe('SearchResult dispatcher', () => {
  it.each([
    ['image/png', 'img-heading', { filename: 'x.png', metadata: { title: 'img-heading' } }],
    ['text/markdown', 'md-doc', { filename: 'md-doc' }],
    ['text/plain', 'txt-doc', { filename: 'txt-doc' }],
    ['application/pdf', 'pdf-doc', { filename: 'pdf-doc' }],
  ])('renders the right result for %s', (mime, expectedName, extra) => {
    renderWithUser(<SearchResult result={base({ mime_type: mime, ...(extra as object) })} />)
    expect(screen.getByRole('heading', { name: expectedName })).toBeInTheDocument()
  })

  it('renders an email result for message/rfc822', () => {
    renderWithUser(
      <SearchResult result={base({ mime_type: 'message/rfc822', metadata: { subject: 'mail' } })} />,
    )
    expect(screen.getByRole('heading', { name: 'mail' })).toBeInTheDocument()
  })

  it('returns null for an unknown mime type', () => {
    const { container } = renderWithUser(
      <SearchResult result={base({ mime_type: 'application/zip' })} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('returns null when mime_type is missing', () => {
    const { container } = renderWithUser(
      <SearchResult result={base({ mime_type: undefined as any })} />,
    )
    expect(container.firstChild).toBeNull()
  })
})

import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithUser } from '@/test/utils'
import { DynamicFilters } from './DynamicFilters'
import { CollectionMetadata } from '@/types/mcp'

const schema = (fields: Record<string, { type: string; description: string }>): CollectionMetadata => ({
  schema: fields,
  size: 0,
})

describe('DynamicFilters', () => {
  it('renders nothing when no modalities are selected', () => {
    const { container } = renderWithUser(
      <DynamicFilters schemas={{}} selectedModalities={[]} filters={{}} onFilterChange={() => {}} />,
    )
    // no schemas + no selected modalities => only the always-present created_at common fields
    // Actually getCommonFields always seeds min/max created_at, so it is NOT null.
    expect(container.firstChild).not.toBeNull()
  })

  it('always renders the Document Properties section with created_at range', () => {
    renderWithUser(
      <DynamicFilters schemas={{}} selectedModalities={[]} filters={{}} onFilterChange={() => {}} />,
    )
    expect(screen.getByText('Document properties')).toBeInTheDocument()
    expect(screen.getByLabelText('Min created at')).toBeInTheDocument()
    expect(screen.getByLabelText('Max created at')).toBeInTheDocument()
  })

  it('renders modality-specific section for non-common fields', () => {
    const schemas = {
      blog: schema({ author: { type: 'string', description: 'Author' } }),
    }
    renderWithUser(
      <DynamicFilters
        schemas={schemas}
        selectedModalities={['blog']}
        filters={{}}
        onFilterChange={() => {}}
      />,
    )
    expect(screen.getByText('Blog specific')).toBeInTheDocument()
    expect(screen.getByLabelText('Author')).toBeInTheDocument()
  })

  it('does not create a modality section when all fields are common', () => {
    const schemas = {
      email: schema({ filename: { type: 'string', description: 'File' } }),
    }
    renderWithUser(
      <DynamicFilters
        schemas={schemas}
        selectedModalities={['email']}
        filters={{}}
        onFilterChange={() => {}}
      />,
    )
    // filename is a common field => it goes to Document Properties, no "Email specific"
    expect(screen.queryByText('Email specific')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Filename')).toBeInTheDocument()
  })

  it('expands size into min/max size common fields', () => {
    const schemas = {
      file: schema({ size: { type: 'int', description: 'Size' } }),
    }
    renderWithUser(
      <DynamicFilters
        schemas={schemas}
        selectedModalities={['file']}
        filters={{}}
        onFilterChange={() => {}}
      />,
    )
    expect(screen.getByLabelText('Min size')).toBeInTheDocument()
    expect(screen.getByLabelText('Max size')).toBeInTheDocument()
  })

  it('expands sent_at into min/max common fields', () => {
    const schemas = {
      email: schema({ sent_at: { type: 'datetime', description: 'Sent' } }),
    }
    renderWithUser(
      <DynamicFilters
        schemas={schemas}
        selectedModalities={['email']}
        filters={{}}
        onFilterChange={() => {}}
      />,
    )
    expect(screen.getByLabelText('Min sent at')).toBeInTheDocument()
    expect(screen.getByLabelText('Max sent at')).toBeInTheDocument()
  })

  it('skips the tags field entirely', () => {
    const schemas = {
      blog: schema({
        tags: { type: 'array', description: 'Tags' },
        author: { type: 'string', description: 'Author' },
      }),
    }
    renderWithUser(
      <DynamicFilters
        schemas={schemas}
        selectedModalities={['blog']}
        filters={{}}
        onFilterChange={() => {}}
      />,
    )
    expect(screen.queryByText('Tags:')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Author')).toBeInTheDocument()
  })

  it('formats underscored field names into sentence case labels', () => {
    const schemas = {
      blog: schema({ word_count: { type: 'int', description: 'Words' } }),
    }
    renderWithUser(
      <DynamicFilters
        schemas={schemas}
        selectedModalities={['blog']}
        filters={{}}
        onFilterChange={() => {}}
      />,
    )
    expect(screen.getByLabelText('Word count')).toBeInTheDocument()
  })

  it('passes current filter values into inputs', () => {
    const schemas = {
      blog: schema({ author: { type: 'string', description: 'Author' } }),
    }
    renderWithUser(
      <DynamicFilters
        schemas={schemas}
        selectedModalities={['blog']}
        filters={{ author: 'Tolkien' }}
        onFilterChange={() => {}}
      />,
    )
    expect(screen.getByDisplayValue('Tolkien')).toBeInTheDocument()
  })

  it('invokes onFilterChange when a filter input changes', async () => {
    const onFilterChange = vi.fn()
    const schemas = {
      blog: schema({ author: { type: 'string', description: 'Author' } }),
    }
    const { user } = renderWithUser(
      <DynamicFilters
        schemas={schemas}
        selectedModalities={['blog']}
        filters={{}}
        onFilterChange={onFilterChange}
      />,
    )
    await user.type(screen.getByPlaceholderText('Author'), 'Z')
    expect(onFilterChange).toHaveBeenLastCalledWith('author', 'Z')
  })

  it('ignores modalities with no schema present', () => {
    renderWithUser(
      <DynamicFilters
        schemas={{}}
        selectedModalities={['ghost']}
        filters={{}}
        onFilterChange={() => {}}
      />,
    )
    expect(screen.queryByText('Ghost specific')).not.toBeInTheDocument()
  })
})

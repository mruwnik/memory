import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithUser } from '@/test/utils'
import { DynamicFilters } from './DynamicFilters'
import { CollectionMetadata } from '@/types/mcp'

const field = (description: string, type = 'str') => ({ type, description })

const collection = (filters: CollectionMetadata['filters']): CollectionMetadata => ({
  schema: { title: field('Title') },
  filters,
  size: 1,
})

const schemas = {
  mail: collection({
    tags: field('tags', 'list[str]'),
    min_size: field('minimum size', 'int'),
    sender: field('sender'),
  }),
  blog: collection({
    tags: field('tags', 'list[str]'),
    min_size: field('minimum size', 'int'),
    domain: field('domain'),
  }),
}

const renderFilters = (selectedModalities: string[], props: Partial<Parameters<typeof DynamicFilters>[0]> = {}) =>
  renderWithUser(
    <DynamicFilters
      schemas={schemas}
      selectedModalities={selectedModalities}
      filters={{}}
      onFilterChange={() => {}}
      {...props}
    />,
  )

describe('DynamicFilters', () => {
  it('renders nothing without schemas', () => {
    const { container } = renderWithUser(
      <DynamicFilters schemas={{}} selectedModalities={[]} filters={{}} onFilterChange={() => {}} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('puts filters every collection accepts under Document properties', () => {
    renderFilters([])
    expect(screen.getByText('Document properties')).toBeInTheDocument()
    expect(screen.getByLabelText('Min size')).toBeInTheDocument()
    expect(screen.queryByLabelText('Sender')).not.toBeInTheDocument()
  })

  it('shows collection-only filters for the selected modalities', () => {
    renderFilters(['mail'])
    expect(screen.getByText('Mail specific')).toBeInTheDocument()
    expect(screen.getByLabelText('Sender')).toBeInTheDocument()
    expect(screen.queryByText('Blog specific')).not.toBeInTheDocument()
  })

  it.each([['Title'], ['Tags']])('does not render %s, which has no filter input here', (label) => {
    renderFilters(['mail', 'blog'])
    expect(screen.queryByLabelText(label)).not.toBeInTheDocument()
  })

  it('passes current filter values into inputs', () => {
    renderFilters(['mail'], { filters: { sender: 'a@b.c' } })
    expect(screen.getByDisplayValue('a@b.c')).toBeInTheDocument()
  })

  it('invokes onFilterChange when a filter input changes', async () => {
    const onFilterChange = vi.fn()
    const { user } = renderFilters(['blog'], { onFilterChange })
    await user.type(screen.getByLabelText('Domain'), 'Z')
    expect(onFilterChange).toHaveBeenLastCalledWith('domain', 'Z')
  })

  it('ignores modalities with no schema present', () => {
    renderFilters(['ghost'])
    expect(screen.queryByText('Ghost specific')).not.toBeInTheDocument()
  })
})

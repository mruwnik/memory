import { describe, it, expect, vi } from 'vitest'
import { screen } from '@testing-library/react'
import { renderWithUser } from '@/test/utils'
import { SelectableTags } from './SelectableTags'

const tags = (sel: Record<string, boolean>) => sel

describe('SelectableTags', () => {
  it('renders title with selected count in summary', () => {
    renderWithUser(
      <SelectableTags title="Tags" tags={tags({ a: true, b: false, c: true })} onSelect={() => {}} />,
    )
    expect(screen.getByText('Tags (2 selected)')).toBeInTheDocument()
    expect(screen.getByText('(2/3)')).toBeInTheDocument()
  })

  it('renders one button per tag with aria-pressed reflecting selection', () => {
    renderWithUser(
      <SelectableTags title="Tags" tags={tags({ alpha: true, beta: false })} onSelect={() => {}} />,
    )
    expect(screen.getByRole('button', { name: 'alpha' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'beta' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('toggles a tag via onSelect with the inverted value', async () => {
    const onSelect = vi.fn()
    const { user } = renderWithUser(
      <SelectableTags title="Tags" tags={tags({ alpha: false })} onSelect={onSelect} />,
    )
    await user.click(screen.getByRole('button', { name: 'alpha' }))
    expect(onSelect).toHaveBeenCalledWith('alpha', true)
  })

  it('deselects a currently-selected tag', async () => {
    const onSelect = vi.fn()
    const { user } = renderWithUser(
      <SelectableTags title="Tags" tags={tags({ alpha: true })} onSelect={onSelect} />,
    )
    await user.click(screen.getByRole('button', { name: 'alpha' }))
    expect(onSelect).toHaveBeenCalledWith('alpha', false)
  })

  describe('All / None batch buttons', () => {
    it('uses onBatchUpdate to select all', async () => {
      const onBatch = vi.fn()
      const { user } = renderWithUser(
        <SelectableTags
          title="Tags"
          tags={tags({ a: false, b: true })}
          onSelect={() => {}}
          onBatchUpdate={onBatch}
        />,
      )
      await user.click(screen.getByRole('button', { name: 'All' }))
      expect(onBatch).toHaveBeenCalledWith({ a: true, b: true })
    })

    it('uses onBatchUpdate to deselect all', async () => {
      const onBatch = vi.fn()
      const { user } = renderWithUser(
        <SelectableTags
          title="Tags"
          tags={tags({ a: true, b: true })}
          onSelect={() => {}}
          onBatchUpdate={onBatch}
        />,
      )
      await user.click(screen.getByRole('button', { name: 'None' }))
      expect(onBatch).toHaveBeenCalledWith({ a: false, b: false })
    })

    it('falls back to per-tag onSelect for select-all when no onBatchUpdate', async () => {
      const onSelect = vi.fn()
      const { user } = renderWithUser(
        <SelectableTags title="Tags" tags={tags({ a: false, b: true })} onSelect={onSelect} />,
      )
      await user.click(screen.getByRole('button', { name: 'All' }))
      // only the unselected tag gets toggled
      expect(onSelect).toHaveBeenCalledTimes(1)
      expect(onSelect).toHaveBeenCalledWith('a', true)
    })

    it('falls back to per-tag onSelect for deselect-all when no onBatchUpdate', async () => {
      const onSelect = vi.fn()
      const { user } = renderWithUser(
        <SelectableTags title="Tags" tags={tags({ a: true, b: false })} onSelect={onSelect} />,
      )
      await user.click(screen.getByRole('button', { name: 'None' }))
      expect(onSelect).toHaveBeenCalledTimes(1)
      expect(onSelect).toHaveBeenCalledWith('a', false)
    })

    it('disables All when everything is already selected', () => {
      renderWithUser(
        <SelectableTags title="Tags" tags={tags({ a: true, b: true })} onSelect={() => {}} />,
      )
      expect(screen.getByRole('button', { name: 'All' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'None' })).toBeEnabled()
    })

    it('disables None when nothing is selected', () => {
      renderWithUser(
        <SelectableTags title="Tags" tags={tags({ a: false, b: false })} onSelect={() => {}} />,
      )
      expect(screen.getByRole('button', { name: 'None' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'All' })).toBeEnabled()
    })
  })

  describe('searchable mode', () => {
    it('does not render a search box when not searchable', () => {
      renderWithUser(
        <SelectableTags title="Tags" tags={tags({ a: false })} onSelect={() => {}} />,
      )
      expect(screen.queryByPlaceholderText('Search tags...')).not.toBeInTheDocument()
    })

    it('renders a search box when searchable', () => {
      renderWithUser(
        <SelectableTags title="Modalities" tags={tags({ a: false })} onSelect={() => {}} searchable />,
      )
      expect(screen.getByPlaceholderText('Search modalities...')).toBeInTheDocument()
    })

    it('filters the visible tags by search term (case-insensitive)', async () => {
      const { user } = renderWithUser(
        <SelectableTags
          title="Tags"
          tags={tags({ apple: false, banana: false, apricot: true })}
          onSelect={() => {}}
          searchable
        />,
      )
      await user.type(screen.getByPlaceholderText('Search tags...'), 'AP')
      expect(screen.getByRole('button', { name: 'apple' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'apricot' })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'banana' })).not.toBeInTheDocument()
    })

    it('shows filtered counts only while a search term is present', async () => {
      const { user } = renderWithUser(
        <SelectableTags
          title="Tags"
          tags={tags({ apple: true, banana: false })}
          onSelect={() => {}}
          searchable
        />,
      )
      expect(screen.queryByText(/Showing/)).not.toBeInTheDocument()
      await user.type(screen.getByPlaceholderText('Search tags...'), 'apple')
      expect(screen.getByText('Showing 1/1')).toBeInTheDocument()
    })
  })

  it('renders no tag buttons for an empty tag set', () => {
    renderWithUser(<SelectableTags title="Tags" tags={tags({})} onSelect={() => {}} />)
    const summary = screen.getByText('Tags (0 selected)')
    expect(summary).toBeInTheDocument()
    // All/None still present
    expect(screen.getByRole('button', { name: 'All' })).toBeInTheDocument()
  })

  it('keeps selected counts independent from filtered set', async () => {
    const { user } = renderWithUser(
      <SelectableTags
        title="Tags"
        tags={tags({ apple: true, banana: true, cherry: false })}
        onSelect={() => {}}
        searchable
      />,
    )
    // overall summary stays 2 selected
    expect(screen.getByText('Tags (2 selected)')).toBeInTheDocument()
    await user.type(screen.getByPlaceholderText('Search tags...'), 'an')
    // banana matches 'an' and is selected; apple/cherry filtered out
    expect(screen.getByText('Showing 1/1')).toBeInTheDocument()
  })
})

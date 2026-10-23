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

  it('renders no tag buttons for an empty tag set', () => {
    renderWithUser(<SelectableTags title="Tags" tags={tags({})} onSelect={() => {}} />)
    const summary = screen.getByText('Tags (0 selected)')
    expect(summary).toBeInTheDocument()
    // All/None still present
    expect(screen.getByRole('button', { name: 'All' })).toBeInTheDocument()
  })
})

import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ConfirmButton, Modal, SplitView } from './ui'

describe('ConfirmButton', () => {
  it('focuses Cancel, keeps Tab inside the dialog, and returns focus to the trigger', async () => {
    const user = userEvent.setup()
    render(<ConfirmButton confirm="Delete it?" onConfirm={vi.fn()} />)
    const trigger = screen.getByRole('button', { name: 'Delete' })
    await user.click(trigger)

    const cancel = screen.getByRole('button', { name: 'Cancel' })
    expect(cancel).toHaveFocus()
    await user.tab()
    await user.tab()
    expect(cancel).toHaveFocus()

    await user.click(cancel)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })
})

describe('Modal', () => {
  it('ignores backdrop clicks and closes on Escape', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(<Modal title="Edit" onClose={onClose}><input aria-label="Name" /></Modal>)

    await user.click(screen.getByRole('dialog').parentElement!)
    expect(onClose).not.toHaveBeenCalled()

    await user.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalledOnce()
  })
})

describe('SplitView', () => {
  it('shows the placeholder on narrow screens when nothing is selected', () => {
    render(<SplitView list={<p>list</p>} detail={null} placeholder={<p>fleet</p>} onBack={vi.fn()} />)
    expect(screen.getByText('fleet').closest('.hidden')).toBeNull()
  })
})

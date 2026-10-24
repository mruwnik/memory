import { describe, it, expect, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ConfirmButton, Field, Input, Modal, SplitView, useActionNotice } from './ui'

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

  it('offers no × and ignores Escape without onClose, so only its content can close it', async () => {
    const user = userEvent.setup()
    render(<Modal title="Key"><button>Done</button></Modal>)

    await user.keyboard('{Escape}')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Close' })).not.toBeInTheDocument()
  })
})

describe('SplitView', () => {
  it('shows the placeholder on narrow screens when nothing is selected', () => {
    render(<SplitView list={<p>list</p>} detail={null} placeholder={<p>fleet</p>} onBack={vi.fn()} />)
    expect(screen.getByText('fleet').closest('.hidden')).toBeNull()
  })
})

describe('Field', () => {
  const email = false as boolean
  it('labels the one control its conditional children render, and describes it with the hint and error', () => {
    render(
      <Field label="Target" hint="Who gets it" error="Required">
        {email && <Input type="email" />}
        {!email && <Input type="text" />}
      </Field>,
    )
    const input = screen.getByRole('textbox', { name: 'Target' })
    expect(input).toHaveAccessibleDescription('Who gets it Required')
    expect(input).toHaveAttribute('aria-invalid', 'true')
  })
})

describe('useActionNotice', () => {
  const Harness = ({ action }: { action: () => Promise<unknown> }) => {
    const { run, notice } = useActionNotice()
    return <>{notice}<button onClick={() => run(action, 'Sync queued')}>Go</button></>
  }

  it.each([
    [() => Promise.reject(new Error('Workspace is paused')), 'alert', 'Workspace is paused'],
    [() => Promise.resolve(), 'status', 'Sync queued'],
  ])('shows the outcome of an action', async (action, role, text) => {
    render(<Harness action={action} />)
    await act(() => userEvent.setup().click(screen.getByRole('button', { name: 'Go' })))
    expect(screen.getByRole(role)).toHaveTextContent(text)
  })
})

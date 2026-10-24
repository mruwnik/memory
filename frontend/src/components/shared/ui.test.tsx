import { describe, it, expect, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { Button, Combobox, ConfirmButton, CopyButton, Field, Fieldset, Input, Modal, SplitView, Tabs, useActionNotice } from './ui'

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

describe('Escape', () => {
  const Nested = ({ onOuterClose }: { onOuterClose: () => void }) => {
    const [inner, setInner] = useState(true)
    return (
      <Modal title="Outer" onClose={onOuterClose}>
        <ConfirmButton confirm="Delete it?" onConfirm={vi.fn()} />
        <Combobox aria-label="Channel" value="" onChange={vi.fn()} options={[{ value: 'c1', label: '#general' }]} />
        {inner && <Modal title="Inner" onClose={() => setInner(false)}><button>Ok</button></Modal>}
      </Modal>
    )
  }

  it('closes only the topmost layer', async () => {
    const user = userEvent.setup()
    const onOuterClose = vi.fn()
    render(<Nested onOuterClose={onOuterClose} />)

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Inner' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Delete it?' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('combobox', { name: 'Channel' }))
    expect(screen.getByRole('listbox')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()

    expect(onOuterClose).not.toHaveBeenCalled()
    await user.keyboard('{Escape}')
    expect(onOuterClose).toHaveBeenCalledOnce()
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

describe('Button', () => {
  it('does not submit its form unless it is a submit button', async () => {
    const onSubmit = vi.fn((e: { preventDefault: () => void }) => e.preventDefault())
    render(<form onSubmit={onSubmit}><Button>Plain</Button><Button type="submit">Save</Button></form>)
    await userEvent.click(screen.getByRole('button', { name: 'Plain' }))
    expect(onSubmit).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSubmit).toHaveBeenCalledOnce()
  })
})

describe('Fieldset', () => {
  it('names the group by its legend, describes it with the hint and error, and disables its controls', () => {
    render(<Fieldset legend="Scopes" hint="Pick some" error="Pick at least one" disabled><input type="checkbox" aria-label="read" /></Fieldset>)
    const group = screen.getByRole('group', { name: 'Scopes' })
    expect(group).toHaveAccessibleDescription('Pick some Pick at least one')
    expect(screen.getByRole('checkbox', { name: 'read' })).toBeDisabled()
  })
})

describe('CopyButton', () => {
  it.each([
    ['works', { writeText: vi.fn().mockResolvedValue(undefined) }, 'Copied'],
    ['is missing, as on plain http', undefined, 'Copy failed'],
  ])('says so when the clipboard %s', async (_, clipboard, outcome) => {
    const user = userEvent.setup()
    Object.defineProperty(navigator, 'clipboard', { value: clipboard, configurable: true })
    render(<CopyButton text="abc" />)
    await user.click(screen.getByRole('button', { name: 'Copy' }))
    expect(await screen.findByRole('button', { name: outcome })).toBeInTheDocument()
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

describe('Tabs', () => {
  const Harness = () => {
    const [value, setValue] = useState('a')
    const options = [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }, { value: 'c', label: 'C' }]
    return <Tabs label="T" options={options} value={value} onChange={setValue}>panel {value}</Tabs>
  }

  it.each([
    ['{ArrowRight}', 'B'],
    ['{ArrowLeft}', 'C'],
    ['{ArrowRight}{ArrowRight}{ArrowRight}', 'A'],
    ['{End}', 'C'],
    ['{End}{Home}', 'A'],
  ])('moves selection and focus with %s and labels the panel by the selected tab', async (keys, name) => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.tab()
    await user.keyboard(keys)
    const tab = screen.getByRole('tab', { name })
    expect(tab).toHaveFocus()
    expect(tab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getAllByRole('tab').filter(t => t.tabIndex === 0)).toEqual([tab])
    expect(screen.getByRole('tabpanel', { name })).toHaveTextContent(`panel ${name.toLowerCase()}`)
  })

  it('scrolls the selected tab into view on mount and on change', () => {
    const scroll = vi.spyOn(Element.prototype, 'scrollIntoView')
    const options = [{ value: 'a', label: 'A' }, { value: 'z', label: 'Z' }]
    const { rerender } = render(<Tabs label="T" options={options} value="z" onChange={vi.fn()} />)
    expect(scroll.mock.contexts.at(-1)).toBe(screen.getByRole('tab', { name: 'Z' }))
    rerender(<Tabs label="T" options={options} value="a" onChange={vi.fn()} />)
    expect(scroll.mock.contexts.at(-1)).toBe(screen.getByRole('tab', { name: 'A' }))
    scroll.mockRestore()
  })
})

import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@/test/utils'
import Combobox, { ComboOption } from './Combobox'

const OPTIONS: ComboOption[] = [
  { value: 'C1', label: 'Acme / #general', group: 'Acme' },
  { value: 'C2', label: 'Acme / #random', group: 'Acme' },
  { value: 'C3', label: 'Beta / #dev', group: 'Beta' },
]

describe('Combobox', () => {
  it('shows the label for the current value while closed', () => {
    render(<Combobox value="C2" onChange={() => {}} options={OPTIONS} />)
    expect(screen.getByRole('combobox')).toHaveValue('Acme / #random')
  })

  it('filters options by typed query and selects one', () => {
    const onChange = vi.fn()
    render(<Combobox value="" onChange={onChange} options={OPTIONS} />)
    const input = screen.getByRole('combobox')
    fireEvent.change(input, { target: { value: 'dev' } })
    expect(screen.queryByText('Acme / #general')).not.toBeInTheDocument()
    fireEvent.mouseDown(screen.getByText('Beta / #dev'))
    expect(onChange).toHaveBeenCalledWith('C3', OPTIONS[2])
  })

  it('lands on the first option when opening with ArrowDown (no skip)', () => {
    const onChange = vi.fn()
    render(<Combobox value="" onChange={onChange} options={OPTIONS} />)
    const input = screen.getByRole('combobox')
    fireEvent.keyDown(input, { key: 'ArrowDown' }) // opens from closed → highlight 0
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith('C1', OPTIONS[0])
  })

  it('emits a custom value (no option) when allowCustom and no match', () => {
    const onChange = vi.fn()
    render(<Combobox value="" onChange={onChange} options={OPTIONS} allowCustom />)
    const input = screen.getByRole('combobox')
    fireEvent.change(input, { target: { value: '99887766' } })
    fireEvent.blur(input)
    expect(onChange).toHaveBeenCalledWith('99887766', undefined)
  })

  it('does not emit a custom value when allowCustom is off', () => {
    const onChange = vi.fn()
    render(<Combobox value="" onChange={onChange} options={OPTIONS} />)
    const input = screen.getByRole('combobox')
    fireEvent.change(input, { target: { value: 'nope' } })
    fireEvent.blur(input)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('delegates filtering to the parent when onQueryChange is set', () => {
    const onQueryChange = vi.fn()
    render(
      <Combobox value="" onChange={() => {}} options={OPTIONS} onQueryChange={onQueryChange} />
    )
    const input = screen.getByRole('combobox')
    fireEvent.change(input, { target: { value: 'ada' } })
    expect(onQueryChange).toHaveBeenCalledWith('ada')
    // async mode shows options unfiltered
    expect(screen.getByText('Acme / #general')).toBeInTheDocument()
  })
})

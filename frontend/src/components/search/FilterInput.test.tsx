import { describe, it, expect, vi } from 'vitest'
import { useState } from 'react'
import { screen } from '@testing-library/react'
import { renderWithUser } from '@/test/utils'
import { FilterInput } from './FilterInput'

// FilterInput is fully controlled (value={value || ''}); without a parent that
// stores updates, typing more than one char has no cumulative effect. This
// wrapper supplies that state so multi-keystroke typing behaves realistically.
const Controlled = ({
  field,
  fieldConfig,
  initial = null,
  onChange,
}: {
  field: string
  fieldConfig: any
  initial?: any
  onChange: (field: string, value: any) => void
}) => {
  const [value, setValue] = useState<any>(initial)
  return (
    <FilterInput
      field={field}
      fieldConfig={fieldConfig}
      value={value}
      onChange={(f, v) => {
        setValue(v)
        onChange(f, v)
      }}
    />
  )
}

describe('FilterInput', () => {
  describe('number fields', () => {
    it.each([
      ['type contains int', 'whatever', { type: 'integer', description: 'an int' }],
      ['field name includes size', 'min_size', { type: 'string', description: 'bytes' }],
    ])('renders a number input when %s', (_label, field, config) => {
      renderWithUser(
        <FilterInput field={field} fieldConfig={config} value={null} onChange={() => {}} />,
      )
      expect(screen.getByRole('spinbutton')).toBeInTheDocument()
    })

    it('parses numeric input and reports it via onChange', async () => {
      const onChange = vi.fn()
      const { user } = renderWithUser(
        <Controlled field="max_size" fieldConfig={{ type: 'int', description: 'bytes' }} onChange={onChange} />,
      )
      await user.type(screen.getByRole('spinbutton'), '42')
      expect(onChange).toHaveBeenLastCalledWith('max_size', 42)
    })

    it('reports null when the number input is cleared', async () => {
      const onChange = vi.fn()
      const { user } = renderWithUser(
        <Controlled field="max_size" fieldConfig={{ type: 'int', description: 'bytes' }} initial={5} onChange={onChange} />,
      )
      await user.clear(screen.getByRole('spinbutton'))
      expect(onChange).toHaveBeenLastCalledWith('max_size', null)
    })

    it('uses the description as placeholder', () => {
      renderWithUser(
        <FilterInput
          field="min_size"
          fieldConfig={{ type: 'int', description: 'Minimum size' }}
          value={null}
          onChange={() => {}}
        />,
      )
      expect(screen.getByPlaceholderText('Minimum size')).toBeInTheDocument()
    })
  })

  describe('date fields', () => {
    it.each([['sent_at'], ['published'], ['created_at'], ['min_created_at']])(
      'renders a date input for %s',
      (field) => {
        const { container } = renderWithUser(
          <FilterInput
            field={field}
            fieldConfig={{ type: 'datetime', description: 'a date' }}
            value=""
            onChange={() => {}}
          />,
        )
        expect(container.querySelector('input[type="date"]')).toBeInTheDocument()
      },
    )

    it('reports the date value via onChange', async () => {
      const onChange = vi.fn()
      const { user, container } = renderWithUser(
        <Controlled field="sent_at" fieldConfig={{ type: 'datetime', description: 'date' }} initial="" onChange={onChange} />,
      )
      const input = container.querySelector('input[type="date"]') as HTMLInputElement
      await user.type(input, '2024-01-15')
      expect(onChange).toHaveBeenLastCalledWith('sent_at', '2024-01-15')
    })
  })

  describe('text fields (default)', () => {
    it('renders a text input for a plain field', () => {
      renderWithUser(
        <FilterInput
          field="author"
          fieldConfig={{ type: 'string', description: 'Author name' }}
          value=""
          onChange={() => {}}
        />,
      )
      expect(screen.getByRole('textbox')).toBeInTheDocument()
      expect(screen.getByPlaceholderText('Author name')).toBeInTheDocument()
    })

    it('reports text via onChange and null when emptied', async () => {
      const onChange = vi.fn()
      const { user } = renderWithUser(
        <Controlled field="author" fieldConfig={{ type: 'string', description: 'Author' }} initial="" onChange={onChange} />,
      )
      await user.type(screen.getByRole('textbox'), 'X')
      expect(onChange).toHaveBeenLastCalledWith('author', 'X')
      await user.clear(screen.getByRole('textbox'))
      expect(onChange).toHaveBeenLastCalledWith('author', null)
    })

    it('shows the current value', () => {
      renderWithUser(
        <FilterInput
          field="author"
          fieldConfig={{ type: 'string', description: 'Author' }}
          value="Tolkien"
          onChange={() => {}}
        />,
      )
      expect(screen.getByRole('textbox')).toHaveValue('Tolkien')
    })
  })
})

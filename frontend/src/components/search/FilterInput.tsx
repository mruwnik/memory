import { useState } from 'react'
import { Input } from '@/components/shared/ui'

const isDateField = (field: string): boolean =>
    field.includes('sent_at') || field.includes('published') || field.includes('created_at')

const isNumberField = (field: string, fieldConfig: any): boolean =>
    fieldConfig.type?.includes('int') || field.includes('size')

const parseNumberValue = (value: string): number | null =>
    value ? parseInt(value) : null

const isListField = (fieldConfig: any): boolean =>
    fieldConfig.type?.startsWith('list') ?? false

const parseListValue = (value: string): string[] | null => {
    const items = value.split(',').map(item => item.trim()).filter(Boolean)
    return items.length ? items : null
}

// Keeps the typed text so a trailing comma survives until the next item is typed.
const ListInput = ({ field, fieldConfig, value, onChange, ...wiring }: FilterInputProps) => {
    const [text, setText] = useState((value ?? []).join(', '))
    return (
        <Input {...wiring} value={text} type="text" placeholder={`${fieldConfig.description} (comma-separated)`}
            onChange={(e) => { setText(e.target.value); onChange(field, parseListValue(e.target.value)) }} />
    )
}

interface FilterInputProps {
    field: string
    fieldConfig: any
    value: any
    onChange: (field: string, value: any) => void
    /** Set by a surrounding `Field`. */
    id?: string
    'aria-describedby'?: string
}

export const FilterInput = (props: FilterInputProps) => {
    const { field, fieldConfig, value, onChange, ...wiring } = props
    if (isListField(fieldConfig)) {
        return <ListInput {...props} />
    }

    if (isNumberField(field, fieldConfig)) {
        return (
            <Input {...wiring} value={value || ''} type="number" placeholder={fieldConfig.description}
                onChange={(e) => onChange(field, parseNumberValue(e.target.value))} />
        )
    }

    if (isDateField(field)) {
        return (
            <Input {...wiring} value={value || ''} type="date"
                onChange={(e) => onChange(field, e.target.value || null)} />
        )
    }

    return (
        <Input {...wiring} value={value || ''} type="text" placeholder={fieldConfig.description}
            onChange={(e) => onChange(field, e.target.value || null)} />
    )
}

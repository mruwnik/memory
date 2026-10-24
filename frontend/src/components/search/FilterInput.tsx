import { Input } from '@/components/shared/ui'

const isDateField = (field: string): boolean =>
    field.includes('sent_at') || field.includes('published') || field.includes('created_at')

const isNumberField = (field: string, fieldConfig: any): boolean =>
    fieldConfig.type?.includes('int') || field.includes('size')

const parseNumberValue = (value: string): number | null =>
    value ? parseInt(value) : null

interface FilterInputProps {
    field: string
    fieldConfig: any
    value: any
    onChange: (field: string, value: any) => void
    /** Set by a surrounding `Field`. */
    id?: string
    'aria-describedby'?: string
}

export const FilterInput = ({ field, fieldConfig, value, onChange, ...wiring }: FilterInputProps) => {
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

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
}

export const FilterInput = ({ field, fieldConfig, value, onChange }: FilterInputProps) => {
    const baseClassName = "w-full py-1 px-2 border border-slate-200 rounded text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/20"

    if (isNumberField(field, fieldConfig)) {
        return (
            <input
                value={value || ''}
                className={baseClassName}
                type="number"
                onChange={(e) => onChange(field, parseNumberValue(e.target.value))}
                placeholder={fieldConfig.description}
            />
        )
    }

    if (isDateField(field)) {
        return (
            <input
                value={value || ''}
                className={baseClassName}
                type="date"
                onChange={(e) => onChange(field, e.target.value || null)}
            />
        )
    }

    return (
        <input
            value={value || ''}
            className={baseClassName}
            type="text"
            onChange={(e) => onChange(field, e.target.value || null)}
            placeholder={fieldConfig.description}
        />
    )
}

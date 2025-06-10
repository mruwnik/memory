// Pure helper functions
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
    const inputProps = {
        value: value || '',
        className: "filter-input"
    }
    
    if (isNumberField(field, fieldConfig)) {
        return (
            <input
                {...inputProps}
                type="number"
                onChange={(e) => onChange(field, parseNumberValue(e.target.value))}
                placeholder={fieldConfig.description}
            />
        )
    }
    
    if (isDateField(field)) {
        return (
            <input
                {...inputProps}
                type="date"
                onChange={(e) => onChange(field, e.target.value || null)}
            />
        )
    }
    
    return (
        <input
            {...inputProps}
            type="text"
            onChange={(e) => onChange(field, e.target.value || null)}
            placeholder={fieldConfig.description}
        />
    )
} 
import { FilterInput } from './FilterInput'
import { CollectionMetadata, SchemaArg } from '@/types/mcp'
import { Disclosure, Field, Label } from '@/components/shared/ui'

const formatFieldLabel = (field: string): string =>
    field.replace(/_/g, ' ').replace(/^\w/, l => l.toUpperCase())

const shouldSkipField = (fieldName: string): boolean =>
    ['tags'].includes(fieldName)

const isCommonField = (fieldName: string): boolean =>
    ['size', 'filename', 'content_type', 'mail_message_id', 'sent_at', 'created_at', 'source_id'].includes(fieldName)

const createMinMaxFields = (fieldName: string, fieldConfig: any): [string, any][] => [
    [`min_${fieldName}`, { ...fieldConfig, description: `Min ${fieldConfig.description}` }],
    [`max_${fieldName}`, { ...fieldConfig, description: `Max ${fieldConfig.description}` }]
]

const createSizeFields = (): [string, any][] => [
    ['min_size', { type: 'int', description: 'Minimum size in bytes' }],
    ['max_size', { type: 'int', description: 'Maximum size in bytes' }]
]

const extractSchemaFields = (schema: Record<string, SchemaArg>, includeCommon = true): [string, SchemaArg][] =>
    Object.entries(schema)
        .filter(([fieldName]) => !shouldSkipField(fieldName))
        .filter(([fieldName]) => includeCommon || !isCommonField(fieldName))
        .flatMap(([fieldName, fieldConfig]) =>
            ['sent_at', 'published', 'created_at'].includes(fieldName)
                ? createMinMaxFields(fieldName, fieldConfig)
                : [[fieldName, fieldConfig] as [string, SchemaArg]]
        )

const getCommonFields = (schemas: Record<string, CollectionMetadata>, selectedModalities: string[]): [string, SchemaArg][] => {
    const commonFieldsMap = new Map<string, SchemaArg>()

    createMinMaxFields('created_at', { type: 'datetime', description: 'Creation date' }).forEach(([field, config]) => {
        commonFieldsMap.set(field, config)
    })

    selectedModalities.forEach(modality => {
        const schema = schemas[modality]?.schema
        if (!schema) return

        Object.entries(schema).forEach(([fieldName, fieldConfig]) => {
            if (isCommonField(fieldName)) {
                if (['sent_at', 'created_at'].includes(fieldName)) {
                    createMinMaxFields(fieldName, fieldConfig).forEach(([field, config]) => {
                        commonFieldsMap.set(field, config)
                    })
                } else if (fieldName === 'size') {
                    createSizeFields().forEach(([field, config]) => {
                        commonFieldsMap.set(field, config)
                    })
                } else {
                    commonFieldsMap.set(fieldName, fieldConfig)
                }
            }
        })
    })

    return Array.from(commonFieldsMap.entries())
}

const getModalityFields = (schemas: Record<string, CollectionMetadata>, selectedModalities: string[]): Record<string, [string, SchemaArg][]> => {
    return selectedModalities.reduce((acc, modality) => {
        const schema = schemas[modality]?.schema
        if (!schema) return acc

        const schemaFields = extractSchemaFields(schema, false)

        if (schemaFields.length > 0) {
            acc[modality] = schemaFields
        }

        return acc
    }, {} as Record<string, [string, SchemaArg][]>)
}

interface DynamicFiltersProps {
    schemas: Record<string, CollectionMetadata>
    selectedModalities: string[]
    filters: Record<string, SchemaArg>
    onFilterChange: (field: string, value: SchemaArg) => void
}

export const DynamicFilters = ({
    schemas,
    selectedModalities,
    filters,
    onFilterChange
}: DynamicFiltersProps) => {
    const commonFields = getCommonFields(schemas, selectedModalities)
    const modalityFields = getModalityFields(schemas, selectedModalities)

    if (commonFields.length === 0 && Object.keys(modalityFields).length === 0) {
        return null
    }

    return (
        <div className="space-y-4">
            <Label>Filters</Label>
            <div className="space-y-3">
                {commonFields.length > 0 && (
                    <Disclosure summary="Document properties" open>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                            {commonFields.map(([field, fieldConfig]: [string, SchemaArg]) => (
                                <Field key={field} label={formatFieldLabel(field)}>
                                    <FilterInput
                                        field={field}
                                        fieldConfig={fieldConfig}
                                        value={filters[field]}
                                        onChange={onFilterChange}
                                    />
                                </Field>
                            ))}
                        </div>
                    </Disclosure>
                )}

                {Object.entries(modalityFields).map(([modality, fields]) => (
                    <Disclosure key={modality} summary={`${formatFieldLabel(modality)} specific`}>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                            {fields.map(([field, fieldConfig]: [string, SchemaArg]) => (
                                <Field key={field} label={formatFieldLabel(field)}>
                                    <FilterInput
                                        field={field}
                                        fieldConfig={fieldConfig}
                                        value={filters[field]}
                                        onChange={onFilterChange}
                                    />
                                </Field>
                            ))}
                        </div>
                    </Disclosure>
                ))}
            </div>
        </div>
    )
}

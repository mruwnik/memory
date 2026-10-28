import { FilterInput } from './FilterInput'
import { CollectionMetadata, SchemaArg } from '@/types/mcp'
import { Disclosure, Field } from '@/components/shared/ui'

type Fields = [string, SchemaArg][]

const formatFieldLabel = (field: string): string =>
    field.replace(/_/g, ' ').replace(/^\w/, l => l.toUpperCase())

// Tags have their own pickers in the search form.
const TAG_FILTERS = new Set(['tags', 'exclude_tags'])
const filtersOf = (metadata: CollectionMetadata): Fields =>
    Object.entries(metadata.filters).filter(([name]) => !TAG_FILTERS.has(name))

// With a single collection every filter is shared, which says nothing; keep them under that collection.
const getCommonFields = (schemas: Record<string, CollectionMetadata>): Fields => {
    const [first, ...rest] = Object.values(schemas)
    if (!rest.length) return []
    return filtersOf(first).filter(([name]) => rest.every(metadata => name in metadata.filters))
}

const getModalityFields = (schemas: Record<string, CollectionMetadata>, selectedModalities: string[], common: Fields): Record<string, Fields> => {
    const commonNames = new Set(common.map(([name]) => name))
    return Object.fromEntries(
        selectedModalities
            .filter(modality => schemas[modality])
            .map(modality => [modality, filtersOf(schemas[modality]).filter(([name]) => !commonNames.has(name))] as const)
            .filter(([, fields]) => fields.length > 0)
    )
}

interface DynamicFiltersProps {
    schemas: Record<string, CollectionMetadata>
    selectedModalities: string[]
    filters: Record<string, any>
    onFilterChange: (field: string, value: any) => void
}

export const DynamicFilters = ({
    schemas,
    selectedModalities,
    filters,
    onFilterChange
}: DynamicFiltersProps) => {
    const commonFields = getCommonFields(schemas)
    const modalityFields = getModalityFields(schemas, selectedModalities, commonFields)

    if (commonFields.length === 0 && Object.keys(modalityFields).length === 0) {
        return null
    }

    const renderFields = (fields: Fields) => (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {fields.map(([field, fieldConfig]) => (
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
    )

    return (
        <div className="space-y-4">
            <h3 className="text-sm font-medium text-slate-700">Filters</h3>
            <div className="space-y-3">
                {commonFields.length > 0 && (
                    <Disclosure summary="Document properties" open>
                        {renderFields(commonFields)}
                    </Disclosure>
                )}

                {Object.entries(modalityFields).map(([modality, fields]) => (
                    <Disclosure key={modality} summary={`${formatFieldLabel(modality)} specific`}>
                        {renderFields(fields)}
                    </Disclosure>
                ))}
            </div>
        </div>
    )
}

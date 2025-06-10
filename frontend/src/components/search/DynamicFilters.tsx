import { FilterInput } from './FilterInput'
import { CollectionMetadata, SchemaArg } from '@/types/mcp'

// Pure helper functions for schema processing
const formatFieldLabel = (field: string): string => 
    field.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())

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
    
    // Manually add created_at fields even if not in schema
    createMinMaxFields('created_at', { type: 'datetime', description: 'Creation date' }).forEach(([field, config]) => {
        commonFieldsMap.set(field, config)
    })
    
    selectedModalities.forEach(modality => {
        const schema = schemas[modality].schema
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
        const schema = schemas[modality].schema
        if (!schema) return acc
        
        const schemaFields = extractSchemaFields(schema, false) // Exclude common fields
        
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
        <div className="search-option">
            <label>Filters:</label>
            <div className="modality-filters">
                {/* Common/Document Properties Section */}
                {commonFields.length > 0 && (
                    <details className="modality-filter-group" open>
                        <summary className="modality-filter-title">
                            Document Properties
                        </summary>
                        <div className="filters-grid">
                            {commonFields.map(([field, fieldConfig]: [string, SchemaArg]) => (
                                <div key={field} className="filter-field">
                                    <label className="filter-label">
                                        {formatFieldLabel(field)}:
                                    </label>
                                    <FilterInput
                                        field={field}
                                        fieldConfig={fieldConfig}
                                        value={filters[field]}
                                        onChange={onFilterChange}
                                    />
                                </div>
                            ))}
                        </div>
                    </details>
                )}
                
                {/* Modality-specific sections */}
                {Object.entries(modalityFields).map(([modality, fields]) => (
                    <details key={modality} className="modality-filter-group">
                        <summary className="modality-filter-title">
                            {formatFieldLabel(modality)} Specific
                        </summary>
                        <div className="filters-grid">
                            {fields.map(([field, fieldConfig]: [string, SchemaArg]) => (
                                <div key={field} className="filter-field">
                                    <label className="filter-label">
                                        {formatFieldLabel(field)}:
                                    </label>
                                    <FilterInput
                                        field={field}
                                        fieldConfig={fieldConfig}
                                        value={filters[field]}
                                        onChange={onFilterChange}
                                    />
                                </div>
                            ))}
                        </div>
                    </details>
                ))}
            </div>
        </div>
    )
} 
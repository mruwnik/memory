import { useMCP } from '@/hooks/useMCP'
import { useEffect, useState } from 'react'
import { DynamicFilters } from './DynamicFilters'
import { SelectableTags } from './SelectableTags'
import { CollectionMetadata } from '@/types/mcp'

type Filter = {
    tags?: string[]
    source_ids?: string[]
    [key: string]: any
}

type SearchConfig = {
    previews: boolean
    useScores: boolean
    limit: number
}

export interface SearchParams {
    query: string
    modalities: string[]
    filters: Filter
    config: SearchConfig
}

interface SearchFormProps {
    isLoading: boolean
    onSearch: (params: SearchParams) => void
}



// Pure helper functions for SearchForm
const createFlags = (items: string[], defaultValue = false): Record<string, boolean> => 
    items.reduce((acc, item) => ({ ...acc, [item]: defaultValue }), {})

const getSelectedItems = (items: Record<string, boolean>): string[] => 
    Object.entries(items).filter(([_, selected]) => selected).map(([key]) => key)

const cleanFilters = (filters: Record<string, any>): Record<string, any> => 
    Object.entries(filters)
        .filter(([_, value]) => value !== null && value !== '' && value !== undefined)
        .reduce((acc, [key, value]) => ({ ...acc, [key]: value }), {})

export const SearchForm = ({ isLoading, onSearch }: SearchFormProps) => {
    const [query, setQuery] = useState('')
    const [previews, setPreviews] = useState(false)
    const [useScores, setUseScores] = useState(false)
    const [modalities, setModalities] = useState<Record<string, boolean>>({})
    const [schemas, setSchemas] = useState<Record<string, CollectionMetadata>>({})
    const [tags, setTags] = useState<Record<string, boolean>>({})
    const [dynamicFilters, setDynamicFilters] = useState<Record<string, any>>({})
    const [limit, setLimit] = useState(10)
    const { getMetadataSchemas, getTags } = useMCP()
    
    useEffect(() => {
        const setupFilters = async () => {
           const [schemas, tags] = await Promise.all([
               getMetadataSchemas(),
               getTags()
           ])
           setSchemas(schemas)
           setModalities(createFlags(Object.keys(schemas), true))
           setTags(createFlags(tags))
        }
        setupFilters()
    }, [getMetadataSchemas, getTags])

    const handleFilterChange = (field: string, value: any) => 
        setDynamicFilters(prev => ({ ...prev, [field]: value }))

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault()
        
        onSearch({
            query,
            modalities: getSelectedItems(modalities),
            config: {
                previews,
                useScores,
                limit
            },
            filters: {
                tags: getSelectedItems(tags),
                ...cleanFilters(dynamicFilters)
            },
        })
    }
    
    return (
        <form onSubmit={handleSubmit} className="search-form">
            <div className="search-input-group">
                <input
                    type="text"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search your knowledge base..."
                    className="search-input"
                    required
                />
                <button type="submit" disabled={isLoading} className="search-btn">
                    {isLoading ? 'Searching...' : 'Search'}
                </button>
            </div>

            <div className="search-options">
                <div className="search-option">
                    <label>
                        <input
                            type="checkbox"
                            checked={previews}
                            onChange={(e) => setPreviews(e.target.checked)}
                        />
                        Include content previews
                    </label>
                </div>
                <div className="search-option">
                    <label>
                        <input
                            type="checkbox"
                            checked={useScores}
                            onChange={(e) => setUseScores(e.target.checked)}
                        />
                        Score results with a LLM before returning
                    </label>
                </div>

                <SelectableTags 
                    title="Modalities" 
                    className="modality-checkboxes" 
                    tags={modalities} 
                    onSelect={(tag, selected) => setModalities({ ...modalities, [tag]: selected })}
                    onBatchUpdate={(updates) => setModalities(updates)}
                />

                <SelectableTags 
                    title="Tags" 
                    className="tags-container" 
                    tags={tags} 
                    onSelect={(tag, selected) => setTags({ ...tags, [tag]: selected })}
                    onBatchUpdate={(updates) => setTags(updates)}
                    searchable={true}
                />

                <DynamicFilters
                    schemas={schemas}
                    selectedModalities={getSelectedItems(modalities)}
                    filters={dynamicFilters}
                    onFilterChange={handleFilterChange}
                />

                <div className="search-option">
                    <label>
                        Max Results:
                        <input
                            type="number"
                            value={limit}
                            onChange={(e) => setLimit(parseInt(e.target.value) || 10)}
                            min={1}
                            max={100}
                            className="limit-input"
                        />
                    </label>
                </div>
            </div>
        </form>
    )
}

export default SearchForm
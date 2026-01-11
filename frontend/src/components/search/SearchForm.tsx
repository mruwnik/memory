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
    useBm25?: boolean
    useHyde?: boolean
    useReranking?: boolean
    useQueryAnalysis?: boolean
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
    const [useBm25, setUseBm25] = useState<boolean | undefined>(true)
    const [useHyde, setUseHyde] = useState<boolean | undefined>(true)
    const [useReranking, setUseReranking] = useState<boolean | undefined>(true)
    const [useQueryAnalysis, setUseQueryAnalysis] = useState<boolean | undefined>(true)
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
                limit,
                useBm25,
                useHyde,
                useReranking,
                useQueryAnalysis,
            },
            filters: {
                tags: getSelectedItems(tags),
                ...cleanFilters(dynamicFilters)
            },
        })
    }

    return (
        <form onSubmit={handleSubmit} className="bg-white p-8 rounded-xl shadow-md mb-8">
            <div className="flex gap-4 items-center mb-6">
                <input
                    type="text"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search your knowledge base..."
                    className="flex-1 py-3 px-4 border border-slate-200 rounded-lg text-base transition-colors focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
                    required
                />
                <button
                    type="submit"
                    disabled={isLoading}
                    className="bg-primary text-white border-none py-3 px-6 rounded-lg text-base font-medium cursor-pointer transition-colors hover:bg-primary-dark disabled:bg-gray-400 disabled:cursor-not-allowed"
                >
                    {isLoading ? 'Searching...' : 'Search'}
                </button>
            </div>

            <div className="space-y-4">
                <label className="flex items-center gap-2 cursor-pointer">
                    <input
                        type="checkbox"
                        checked={previews}
                        onChange={(e) => setPreviews(e.target.checked)}
                        className="w-4 h-4 rounded border-gray-300 text-primary focus:ring-primary"
                    />
                    Include content previews
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                    <input
                        type="checkbox"
                        checked={useScores}
                        onChange={(e) => setUseScores(e.target.checked)}
                        className="w-4 h-4 rounded border-gray-300 text-primary focus:ring-primary"
                    />
                    Score results with a LLM before returning
                </label>

                <details className="border border-slate-200 rounded-lg p-4">
                    <summary className="cursor-pointer font-medium text-slate-700">Search Enhancements</summary>
                    <div className="mt-4 space-y-3 pl-4">
                        <label className="flex items-center gap-2 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={useBm25}
                                onChange={(e) => setUseBm25(e.target.checked)}
                                className="w-4 h-4 rounded border-gray-300 text-primary focus:ring-primary"
                            />
                            BM25 keyword search
                        </label>
                        <label className="flex items-center gap-2 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={useHyde}
                                onChange={(e) => setUseHyde(e.target.checked)}
                                className="w-4 h-4 rounded border-gray-300 text-primary focus:ring-primary"
                            />
                            HyDE (hypothetical document expansion)
                        </label>
                        <label className="flex items-center gap-2 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={useReranking}
                                onChange={(e) => setUseReranking(e.target.checked)}
                                className="w-4 h-4 rounded border-gray-300 text-primary focus:ring-primary"
                            />
                            Reranking (cross-encoder)
                        </label>
                        <label className="flex items-center gap-2 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={useQueryAnalysis}
                                onChange={(e) => setUseQueryAnalysis(e.target.checked)}
                                className="w-4 h-4 rounded border-gray-300 text-primary focus:ring-primary"
                            />
                            Query analysis (LLM-based)
                        </label>
                    </div>
                </details>

                <SelectableTags
                    title="Modalities"
                    tags={modalities}
                    onSelect={(tag, selected) => setModalities({ ...modalities, [tag]: selected })}
                    onBatchUpdate={(updates) => setModalities(updates)}
                />

                <SelectableTags
                    title="Tags"
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

                <label className="flex items-center gap-2">
                    Max Results:
                    <input
                        type="number"
                        value={limit}
                        onChange={(e) => setLimit(parseInt(e.target.value) || 10)}
                        min={1}
                        max={100}
                        className="w-20 py-1 px-2 border border-slate-200 rounded text-sm"
                    />
                </label>
            </div>
        </form>
    )
}

export default SearchForm

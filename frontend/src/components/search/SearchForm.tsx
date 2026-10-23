import { useMCP } from '@/hooks/useMCP'
import { useEffect, useState } from 'react'
import { DynamicFilters } from './DynamicFilters'
import { SelectableTags } from './SelectableTags'
import { CollectionMetadata } from '@/types/mcp'
import { Button, Checkbox, Field, Input, MultiSelect } from '@/components/shared/ui'

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
        setupFilters().catch((e) => {
            console.error('Failed to load search filters:', e)
        })
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
                <Input
                    type="text"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search your knowledge base..."
                    className="flex-1 text-base"
                    required
                />
                <Button variant="primary"
                    type="submit"
                    disabled={isLoading}
                >
                    {isLoading ? 'Searching...' : 'Search'}
                </Button>
            </div>

            <div className="space-y-4">
                <Checkbox checked={previews} onChange={(e) => setPreviews(e.target.checked)} label="Include content previews" />
                <Checkbox checked={useScores} onChange={(e) => setUseScores(e.target.checked)} label="Score results with a LLM before returning" />

                <details className="border border-slate-200 rounded-lg p-4">
                    <summary className="cursor-pointer font-medium text-slate-700">Search Enhancements</summary>
                    <div className="mt-4 space-y-3 pl-4">
                        <Checkbox checked={useBm25} onChange={(e) => setUseBm25(e.target.checked)} label="BM25 keyword search" />
                        <Checkbox checked={useHyde} onChange={(e) => setUseHyde(e.target.checked)} label="HyDE (hypothetical document expansion)" />
                        <Checkbox checked={useReranking} onChange={(e) => setUseReranking(e.target.checked)} label="Reranking (cross-encoder)" />
                        <Checkbox checked={useQueryAnalysis} onChange={(e) => setUseQueryAnalysis(e.target.checked)} label="Query analysis (LLM-based)" />
                    </div>
                </details>

                <SelectableTags
                    title="Modalities"
                    tags={modalities}
                    onSelect={(tag, selected) => setModalities({ ...modalities, [tag]: selected })}
                    onBatchUpdate={(updates) => setModalities(updates)}
                />

                <Field label="Tags">
                    <MultiSelect
                        options={Object.keys(tags)}
                        value={Object.keys(tags).filter(t => tags[t])}
                        onChange={selected => setTags(Object.fromEntries(Object.keys(tags).map(t => [t, selected.includes(t)])))}
                        placeholder="Any tag"
                    />
                </Field>

                <DynamicFilters
                    schemas={schemas}
                    selectedModalities={getSelectedItems(modalities)}
                    filters={dynamicFilters}
                    onFilterChange={handleFilterChange}
                />

                <Field label="Max results">
                    <Input
                        type="number"
                        value={limit}
                        onChange={(e) => {
                            const parsed = parseInt(e.target.value, 10)
                            // Clamp to [1, 100] so an in-progress / out-of-range
                            // value can never exceed max= (which would silently
                            // block form submission) or run away on edit.
                            setLimit(Number.isNaN(parsed) ? 1 : Math.min(100, Math.max(1, parsed)))
                        }}
                        onFocus={(e) => e.target.select()}
                        min={1}
                        max={100}
                        className="w-24"
                    />
                </Field>
            </div>
        </form>
    )
}

export default SearchForm

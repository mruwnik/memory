import { useMCP } from '@/hooks/useMCP'
import { useEffect, useRef, useState } from 'react'
import { DynamicFilters } from './DynamicFilters'
import { CollectionMetadata } from '@/types/mcp'
import { Button, Card, Checkbox, Disclosure, Field, Input, MultiSelect, Notice } from '@/components/shared/ui'

type Filter = {
    tags?: string[]
    source_ids?: string[]
    [key: string]: any
}

type SearchConfig = {
    previews: boolean
    useScores: boolean
    limit: number
    /** Keyed by the search tool's `use_*` parameter names. */
    enhancements: Record<string, boolean>
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
    /** The query in the URL: run when it changes (a link, reload, Back), not when this form just submitted it. */
    urlQuery?: string
}

const DEFAULT_LIMIT = 10

const ENHANCEMENTS = [
    ['use_bm25', 'BM25 keyword search'],
    ['use_hyde', 'HyDE (hypothetical document expansion)'],
    ['use_reranking', 'Reranking (cross-encoder)'],
    ['use_query_analysis', 'Query analysis (LLM-based)'],
] as const
const clampLimit = (limit: number | '') => Math.min(100, Math.max(1, limit === '' ? DEFAULT_LIMIT : limit))

const createFlags = (items: string[], defaultValue = false, selected: string[] = []): Record<string, boolean> =>
    Object.fromEntries(items.map(item => [item, defaultValue || selected.includes(item)]))

const getSelectedItems = (items: Record<string, boolean>): string[] =>
    Object.entries(items).filter(([_, selected]) => selected).map(([key]) => key)

const cleanFilters = (filters: Record<string, any>): Record<string, any> =>
    Object.entries(filters)
        .filter(([_, value]) => value !== null && value !== '' && value !== undefined)
        .reduce((acc, [key, value]) => ({ ...acc, [key]: value }), {})

export const SearchForm = ({ isLoading, onSearch, urlQuery = '' }: SearchFormProps) => {
    const [query, setQuery] = useState(urlQuery)
    const [previews, setPreviews] = useState(false)
    const [useScores, setUseScores] = useState(false)
    const [modalities, setModalities] = useState<Record<string, boolean>>({})
    const [schemas, setSchemas] = useState<Record<string, CollectionMetadata>>({})
    const [tags, setTags] = useState<Record<string, boolean>>({})
    const [dynamicFilters, setDynamicFilters] = useState<Record<string, any>>({})
    const [limit, setLimit] = useState<number | ''>(DEFAULT_LIMIT)
    const [enhancements, setEnhancements] = useState<Record<string, boolean>>({})
    const [availableEnhancements, setAvailableEnhancements] = useState<Record<string, boolean>>({})
    const [scoresAvailable, setScoresAvailable] = useState(false)
    const [filtersReady, setFiltersReady] = useState(false)
    const [filtersError, setFiltersError] = useState<string | null>(null)
    const submittedQuery = useRef<string | null>(null)
    const { getMetadataSchemas, getTags, getSearchDefaults } = useMCP()

    useEffect(() => {
        const setupFilters = async () => {
           const [schemas, tags, defaults] = await Promise.all([
               getMetadataSchemas(),
               getTags(),
               getSearchDefaults(),
           ])
           setSchemas(schemas)
           setModalities(createFlags(Object.keys(schemas), true))
           setTags(createFlags(tags))
           const { use_scores = false, ...enhancementDefaults } = defaults
           setEnhancements(enhancementDefaults)
           setAvailableEnhancements(enhancementDefaults)
           setScoresAvailable(use_scores)
        }
        setupFilters()
            .catch(e => setFiltersError(e instanceof Error ? e.message : String(e)))
            .finally(() => setFiltersReady(true))
    }, [getMetadataSchemas, getTags, getSearchDefaults])

    const handleFilterChange = (field: string, value: any) =>
        setDynamicFilters(prev => ({ ...prev, [field]: value }))

    const search = (query: string) => {
        submittedQuery.current = query
        setLimit(clampLimit(limit))
        onSearch({
            query,
            modalities: getSelectedItems(modalities),
            config: {
                previews,
                useScores,
                limit: clampLimit(limit),
                enhancements,
            },
            filters: {
                tags: getSelectedItems(tags),
                ...cleanFilters(dynamicFilters)
            },
        })
    }

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault()
        search(query)
    }

    // Wait for the modalities so a URL search uses the same defaults as a typed one.
    useEffect(() => {
        if (!filtersReady || !urlQuery || urlQuery === submittedQuery.current) return
        setQuery(urlQuery)
        search(urlQuery)
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [urlQuery, filtersReady])

    return (
        <Card>
        <form onSubmit={handleSubmit}>
            {filtersError && <Notice tone="warning" className="mb-4">Couldn't load search filters: {filtersError}</Notice>}
            <div className="flex gap-4 items-center mb-6">
                <Input
                    type="text"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search your knowledge base..."
                    aria-label="Search query"
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
                <div className="flex flex-col gap-2">
                    <Checkbox checked={previews} onChange={(e) => setPreviews(e.target.checked)} label="Include content previews" />
                    <Checkbox checked={useScores} disabled={!scoresAvailable} onChange={(e) => setUseScores(e.target.checked)} label="Score results with an LLM before returning" />
                </div>

                <Disclosure summary="Search enhancements">
                    <div className="flex flex-col gap-3">
                        {ENHANCEMENTS.map(([key, label]) => (
                            <Checkbox key={key} label={label}
                                checked={enhancements[key] ?? false}
                                // The server can only be asked to skip an enhancement, never to add one it has off or no key pays for.
                                disabled={!availableEnhancements[key]}
                                onChange={(e) => setEnhancements(prev => ({ ...prev, [key]: e.target.checked }))} />
                        ))}
                    </div>
                </Disclosure>

                <Field label="Modalities">
                    <MultiSelect
                        options={Object.keys(modalities)}
                        value={getSelectedItems(modalities)}
                        onChange={selected => setModalities(createFlags(Object.keys(modalities), false, selected))}
                        placeholder="All modalities"
                    />
                </Field>
                <div className="flex gap-3 -mt-2">
                    <Button variant="link" size="sm" type="button" onClick={() => setModalities(createFlags(Object.keys(modalities), true))}>
                        Select all
                    </Button>
                    <Button variant="link" size="sm" type="button" onClick={() => setModalities(createFlags(Object.keys(modalities)))}>
                        Clear
                    </Button>
                </div>

                <Field label="Tags">
                    <MultiSelect
                        options={Object.keys(tags)}
                        value={getSelectedItems(tags)}
                        onChange={selected => setTags(createFlags(Object.keys(tags), false, selected))}
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
                        onChange={(e) => setLimit(e.target.value === '' ? '' : Number(e.target.value))}
                        onBlur={() => setLimit(clampLimit(limit))}
                        onFocus={(e) => e.target.select()}
                        min={1}
                        max={100}
                        className="w-24"
                    />
                </Field>
            </div>
        </form>
        </Card>
    )
}

export default SearchForm

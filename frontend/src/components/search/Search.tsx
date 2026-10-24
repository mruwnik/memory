import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMCP } from '@/hooks/useMCP'
import { SearchResult } from './results'
import SearchForm, { SearchParams } from './SearchForm'
import { EmptyState, List, LoadingState, Notice, Page } from '@/components/shared/ui'

const SearchResults = ({ results, isLoading }: { results: any[] | null, isLoading: boolean }) => {
    if (isLoading) {
        return <LoadingState message="Searching..." />
    }
    if (results === null) return null
    return results.length === 0
        ? <EmptyState message="No results found" />
        : <List>{results.map((result, index) => <SearchResult key={index} result={result} />)}</List>
}

// Always mounted, so screen readers hear the count when a search finishes.
const ResultCount = ({ results, isLoading }: { results: any[] | null, isLoading: boolean }) => {
    const count = !isLoading && results !== null ? results.length : null
    return (
        <p role="status" className={count ? 'text-sm text-slate-600' : 'sr-only'}>
            {count !== null && `Found ${count} result${count !== 1 ? 's' : ''}`}
        </p>
    )
}

const Search = () => {
    const [searchParams, setSearchParams] = useSearchParams()
    const urlQuery = searchParams.get('q') ?? ''
    const [results, setResults] = useState<any[] | null>(null)
    const [isLoading, setIsLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const { searchKnowledgeBase } = useMCP()

    useEffect(() => {
        if (!urlQuery) setResults(null)
    }, [urlQuery])

    const handleSearch = async (params: SearchParams) => {
        if (!params.query.trim()) return
        if (params.query !== urlQuery) setSearchParams({ q: params.query })

        setIsLoading(true)
        setError(null)
        try {
            const searchResults = await searchKnowledgeBase(params.query, params.modalities, params.filters, params.config)
            setResults(searchResults || [])
        } catch (e) {
            setError(e instanceof Error ? e.message : 'Search failed')
            setResults(null)
        } finally {
            setIsLoading(false)
        }
    }

    return (
        <Page title="Search">
            <SearchForm isLoading={isLoading} onSearch={handleSearch} urlQuery={urlQuery} />
            {error && <Notice tone="error">{error}</Notice>}
            <ResultCount results={results} isLoading={isLoading} />
            <SearchResults results={results} isLoading={isLoading} />
        </Page>
    )
}

export default Search

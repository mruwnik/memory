import { useState } from 'react'
import { useMCP } from '@/hooks/useMCP'
import { SearchResult } from './results'
import SearchForm, { SearchParams } from './SearchForm'
import { EmptyState, List, LoadingState, Notice, Page } from '@/components/shared/ui'

const SearchResults = ({ results, isLoading }: { results: any[] | null, isLoading: boolean }) => {
    if (isLoading) {
        return <LoadingState message="Searching..." />
    }
    if (results === null) return null
    return (
        <div className="space-y-4">
            {results.length > 0 && (
                <div className="text-sm text-slate-600">
                    Found {results.length} result{results.length !== 1 ? 's' : ''}
                </div>
            )}

            {results.length === 0
                ? <EmptyState message="No results found" />
                : <List>{results.map((result, index) => <SearchResult key={index} result={result} />)}</List>}
        </div>
    )
}


const Search = () => {
    const [results, setResults] = useState<any[] | null>(null)
    const [isLoading, setIsLoading] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const { searchKnowledgeBase } = useMCP()

    const handleSearch = async (params: SearchParams) => {
        if (!params.query.trim()) return

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
            <SearchForm isLoading={isLoading} onSearch={handleSearch} />
            {error && <Notice tone="error">{error}</Notice>}
            <SearchResults results={results} isLoading={isLoading} />
        </Page>
    )
}

export default Search

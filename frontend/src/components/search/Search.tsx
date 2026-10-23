import { useState } from 'react'
import { useMCP } from '@/hooks/useMCP'
import { SearchResult } from './results'
import SearchForm, { SearchParams } from './SearchForm'
import { EmptyState, LoadingState, Page } from '@/components/shared/ui'

const SearchResults = ({ results, isLoading }: { results: any[], isLoading: boolean }) => {
    if (isLoading) {
        return <LoadingState message="Searching..." />
    }
    return (
        <div className="space-y-4">
            {results.length > 0 && (
                <div className="text-sm text-slate-600">
                    Found {results.length} result{results.length !== 1 ? 's' : ''}
                </div>
            )}

            {results.map((result, index) => <SearchResult key={index} result={result} />)}

            {results.length === 0 && <EmptyState message="No results found" />}
        </div>
    )
}


const Search = () => {
    const [results, setResults] = useState([])
    const [isLoading, setIsLoading] = useState(false)
    const { searchKnowledgeBase } = useMCP()

    const handleSearch = async (params: SearchParams) => {
        if (!params.query.trim()) return

        setIsLoading(true)
        try {
            const searchResults = await searchKnowledgeBase(params.query, params.modalities, params.filters, params.config)
            setResults(searchResults || [])
        } catch (error) {
            console.error('Search error:', error)
            setResults([])
        } finally {
            setIsLoading(false)
        }
    }

    return (
        <Page title="Search Knowledge Base">
            <SearchForm isLoading={isLoading} onSearch={handleSearch} />
            <SearchResults results={results} isLoading={isLoading} />
        </Page>
    )
}

export default Search

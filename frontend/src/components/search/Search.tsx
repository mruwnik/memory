import { useState } from 'react'
import { useNavigate } from 'react-router-dom'  
import { useMCP } from '@/hooks/useMCP'
import Loading from '@/components/Loading'
import { SearchResult } from './results'
import SearchForm, { SearchParams } from './SearchForm'

const SearchResults = ({ results, isLoading }: { results: any[], isLoading: boolean }) => {
    if (isLoading) {
        return <Loading message="Searching..." />
    }
    return (
        <div className="search-results">
            {results.length > 0 && (
                <div className="results-count">
                    Found {results.length} result{results.length !== 1 ? 's' : ''}
                </div>
            )}

            {results.map((result, index) => <SearchResult key={index} result={result} />)}

            {results.length === 0 && (
                <div className="no-results">
                    No results found
                </div>
            )}
        </div>
    )
}


const Search = () => {
    const navigate = useNavigate()
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
        <div className="search-view">
            <header className="search-header">
                <button onClick={() => navigate('/ui/dashboard')} className="back-btn">
                    ← Back to Dashboard
                </button>
                <h2>🔍 Search Knowledge Base</h2>
            </header>

            <SearchForm isLoading={isLoading} onSearch={handleSearch} />
            <SearchResults results={results} isLoading={isLoading} />
        </div>
    )
}

export default Search 
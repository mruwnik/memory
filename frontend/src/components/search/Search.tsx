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
        <div className="space-y-4">
            {results.length > 0 && (
                <div className="text-sm text-gray-600 mb-4">
                    Found {results.length} result{results.length !== 1 ? 's' : ''}
                </div>
            )}

            {results.map((result, index) => <SearchResult key={index} result={result} />)}

            {results.length === 0 && (
                <div className="text-center py-12 text-gray-500 bg-white rounded-lg">
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
        <div className="min-h-screen bg-slate-50 p-8 max-w-5xl mx-auto">
            <header className="flex items-center gap-4 mb-8 pb-4 border-b border-slate-200">
                <button
                    onClick={() => navigate('/ui/dashboard')}
                    className="bg-slate-50 text-slate-600 border border-slate-200 py-2 px-4 rounded-md text-sm cursor-pointer transition-all hover:bg-slate-100 hover:text-slate-800"
                >
                    ← Back to Dashboard
                </button>
                <h2 className="text-slate-800 text-2xl font-semibold">🔍 Search Knowledge Base</h2>
            </header>

            <SearchForm isLoading={isLoading} onSearch={handleSearch} />
            <SearchResults results={results} isLoading={isLoading} />
        </div>
    )
}

export default Search

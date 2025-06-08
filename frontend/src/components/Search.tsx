import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import { useMCP } from '../hooks/useMCP'
import { useAuth } from '../hooks/useAuth'
import Loading from './Loading'

type SearchItem = {
    filename: string
    content: string
    chunks: any[]
    tags: string[]
    mime_type: string
    metadata: any
}

const Tag = ({ tags }: { tags: string[] }) => {
    return (
        <div className="tags">
            {tags?.map((tag: string, index: number) => (
                <span key={index} className="tag">{tag}</span>
            ))}
        </div>
    )
}

const formatText = ({ filename, content, chunks, tags }: SearchItem) => {
    return (
        <div className="search-result-card">
            <h4>{filename || 'Untitled'}</h4>
            <Tag tags={tags} />
            <p className="result-content">{content || 'No content available'}</p>
            {chunks && chunks.length > 0 && (
                <details className="result-chunks">
                    <summary>Relevant sections:</summary>
                    {chunks.map(({preview, score}, chunkIndex) => (
                        <div key={chunkIndex} className="chunk">
                            <div className="result-score">Score: {(score || 0).toFixed(3)}</div>
                            <p>{preview}</p>
                        </div>
                    ))}
                </details>
            )}
        </div>
    )
}

const formatMarkdown = ({ filename, content, chunks, tags, metadata }: SearchItem) => {
    return (
        <div className="search-result-card">
            <h4>{filename || 'Untitled'}</h4>
            <Tag tags={tags} />
            <div className="markdown-content">
                <ReactMarkdown>{content || 'No content available'}</ReactMarkdown>
            </div>
            {chunks && chunks.length > 0 && (
                <details className="result-chunks">
                    <summary>Relevant sections:</summary>
                    {chunks.map(({preview, score}, chunkIndex) => (
                        <div key={chunkIndex} className="chunk">
                            <div className="result-score">Score: {(score || 0).toFixed(3)}</div>
                            <div className="markdown-preview">
                                <p>{preview}</p>
                            </div>
                        </div>
                    ))}
                </details>
            )}
        </div>
    )
}

const formatImage = ({ filename, chunks, tags, metadata }: SearchItem) => {
    const title = metadata?.title || filename || 'Untitled'
    const { fetchFile } = useMCP()
    const [mime_type, setMimeType] = useState<string>()
    const [content, setContent] = useState<string>()
    useEffect(() => {
        const fetchImage = async () => {
            const files = await fetchFile(filename.replace('/app/memory_files/', ''))
            const {mime_type, content} = files[0]
            setMimeType(mime_type)
            setContent(content)
        }
        fetchImage()
    }, [filename])
    return (
        <div className="search-result-card">
            <h4>{title}</h4>
            <Tag tags={tags} />
            <div className="image-container">
                {mime_type && mime_type?.startsWith('image/') && <img src={`data:${mime_type};base64,${content}`} alt={title} className="search-result-image"/>}
            </div>
        </div>
    )
}

const SearchResult = ({ result }: { result: SearchItem }) => {
    if (result.mime_type.startsWith('image/')) {
        return formatImage(result)
    }
    if (result.mime_type.startsWith('text/markdown')) {
        console.log(result)
        return formatMarkdown(result)
    }
    if (result.mime_type.startsWith('text/')) {
        return formatText(result)
    }
    return null
}

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

const SearchForm = ({ isLoading, onSearch }: { isLoading: boolean, onSearch: (query: string) => void }) => {
    const [query, setQuery] = useState('')
    return (
        <form onSubmit={(e) => {
            e.preventDefault()
            onSearch(query)
        }} className="search-form">
            <div className="search-input-group">
                <input
                    type="text"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Search your knowledge base..."
                    className="search-input"
                />
                <button type="submit" disabled={isLoading} className="search-btn">
                    {isLoading ? 'Searching...' : 'Search'}
                </button>
            </div>
        </form>
    )
}

const Search = () => {
    const navigate = useNavigate()
    const [results, setResults] = useState([])
    const [isLoading, setIsLoading] = useState(false)
    const { searchKnowledgeBase } = useMCP()

    const handleSearch = async (query: string) => {
        if (!query.trim()) return

        setIsLoading(true)
        try {
            const searchResults = await searchKnowledgeBase(query)
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
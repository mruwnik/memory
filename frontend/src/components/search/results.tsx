import { useState, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import { useMCP } from '@/hooks/useMCP'

export type SearchItem = {
    filename: string
    content: string
    chunks: any[]
    tags: string[]
    mime_type: string
    metadata: any
}

export const Tag = ({ tags }: { tags: string[] }) => {
    return (
        <div className="tags">
            {tags?.map((tag: string, index: number) => (
                <span key={index} className="tag">{tag}</span>
            ))}
        </div>
    )
}

export const TextResult = ({ filename, content, chunks, tags, metadata }: SearchItem) => {
    return (
        <div className="search-result-card">
            <h4>{filename || 'Untitled'}</h4>
            <Tag tags={tags} />
            <Metadata metadata={metadata} />
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

export const MarkdownResult = ({ filename, content, chunks, tags, metadata }: SearchItem) => {
    return (
        <div className="search-result-card">
            <h4>{filename || 'Untitled'}</h4>
            <Tag tags={tags} />
            <Metadata metadata={metadata} />
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

export const ImageResult = ({ filename, tags, metadata }: SearchItem) => {
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

export const Metadata = ({ metadata }: { metadata: any }) => {
    if (!metadata) return null
    return (
        <div className="metadata">
            <ul>    
                {Object.entries(metadata).map(([key, value]) => (
                    <li key={key}>{key}: {typeof value === 'string' ? value : JSON.stringify(value)}</li>
                ))}
            </ul>
        </div>
    )
}

export const PDFResult = ({ filename, content, tags, metadata }: SearchItem) => {
    return (
        <div className="search-result-card">
            <h4>{filename || 'Untitled'}</h4>
            <Tag tags={tags} />
            <a href={`http://localhost:8000/files/${filename}`}>View PDF</a>
            <Metadata metadata={metadata} />
            {content && <div className="markdown-content">
                <details>
                    <summary>View Source</summary>
                    <ReactMarkdown>{content}</ReactMarkdown>
                </details>
            </div>}
        </div>
    )
}

export const EmailResult = ({ content, tags, metadata }: SearchItem) => {
    return (
        <div className="search-result-card">
            <h4>{metadata?.title || metadata?.subject || 'Untitled'}</h4>
            <Tag tags={tags} />
            <Metadata metadata={metadata} />
            {content && <div className="markdown-content">
                <ReactMarkdown>{content}</ReactMarkdown>
            </div>}
        </div>
    )
}

export const SearchResult = ({ result }: { result: SearchItem }) => {
    if (result.mime_type.startsWith('image/')) {
        return <ImageResult {...result} />
    }
    if (result.mime_type.startsWith('text/markdown')) {
        return <MarkdownResult {...result} /> 
    }
    if (result.mime_type.startsWith('text/')) {
        return <TextResult {...result} />
    }
    if (result.mime_type.startsWith('application/pdf')) {
        return <PDFResult {...result} />
    }
    if (result.mime_type.startsWith('message/rfc822')) {
        return <EmailResult {...result} />
    }
    console.log(result)
    return null
}

export default SearchResult
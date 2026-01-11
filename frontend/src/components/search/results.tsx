import ReactMarkdown from 'react-markdown'
import { SERVER_URL } from '@/hooks/useAuth'

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
        <div className="flex flex-wrap gap-1.5 my-2">
            {tags?.map((tag: string, index: number) => (
                <span key={index} className="bg-slate-100 text-slate-600 px-2 py-0.5 rounded text-xs">{tag}</span>
            ))}
        </div>
    )
}

export const TextResult = ({ filename, content, chunks, tags, metadata }: SearchItem) => {
    return (
        <div className="bg-white p-6 rounded-xl shadow-md mb-4">
            <h4 className="text-lg font-semibold text-slate-800 mb-2">{filename || metadata?.title || metadata?.url || 'Untitled'}</h4>
            <Tag tags={tags} />
            <Metadata metadata={metadata} />
            {content && <p className="text-gray-600 mt-3 whitespace-pre-wrap">{content}</p>}
            {chunks && chunks.length > 0 && (
                <details className="mt-4 border-t border-slate-100 pt-4">
                    <summary className="cursor-pointer text-sm font-medium text-slate-600">Relevant sections:</summary>
                    <div className="mt-3 space-y-3">
                        {chunks.map(({preview, score}, chunkIndex) => (
                            <div key={chunkIndex} className="bg-slate-50 p-3 rounded-lg">
                                <div className="text-xs text-primary font-medium mb-1">Score: {(score || 0).toFixed(3)}</div>
                                <p className="text-sm text-gray-600">{preview}</p>
                            </div>
                        ))}
                    </div>
                </details>
            )}
        </div>
    )
}

export const MarkdownResult = ({ filename, content, chunks, tags, metadata }: SearchItem) => {
    return (
        <div className="bg-white p-6 rounded-xl shadow-md mb-4">
            <h4 className="text-lg font-semibold text-slate-800 mb-2">{filename || metadata?.title || metadata?.url || 'Untitled'}</h4>
            <Tag tags={tags} />
            <Metadata metadata={metadata} />
            {content && (
                <div className="prose prose-sm max-w-none mt-3">
                    <ReactMarkdown>{content}</ReactMarkdown>
                </div>
            )}
            {chunks && chunks.length > 0 && (
                <details className="mt-4 border-t border-slate-100 pt-4">
                    <summary className="cursor-pointer text-sm font-medium text-slate-600">Relevant sections:</summary>
                    <div className="mt-3 space-y-3">
                        {chunks.map(({preview, score}, chunkIndex) => (
                            <div key={chunkIndex} className="bg-slate-50 p-3 rounded-lg">
                                <div className="text-xs text-primary font-medium mb-1">Score: {(score || 0).toFixed(3)}</div>
                                <p className="text-sm text-gray-600">{preview}</p>
                            </div>
                        ))}
                    </div>
                </details>
            )}
        </div>
    )
}

export const ImageResult = ({ filename, tags, metadata }: SearchItem) => {
    const title = metadata?.title || filename || 'Untitled'

    return (
        <div className="bg-white p-6 rounded-xl shadow-md mb-4">
            <h4 className="text-lg font-semibold text-slate-800 mb-2">{title}</h4>
            <Tag tags={tags} />
            <Metadata metadata={metadata} />
            <div className="mt-4">
                <img src={`${SERVER_URL}/files/${filename}`} alt={title} className="max-w-full h-auto rounded-lg"/>
            </div>
        </div>
    )
}

const MetadataItem = ({ item, value }: { item: string, value: any }) => {
    if (item === "url") {
        return <li><a href={value} className="text-primary hover:underline">{value}</a></li>
    }
    if (item === "filename") {
        return <li><a href={`${SERVER_URL}/files/${value}`} className="text-primary hover:underline">{value}</a></li>
    }
    if (typeof value === 'string') {
        return <li><span className="text-slate-500">{item}:</span> {value}</li>
    }
    return <li><span className="text-slate-500">{item}:</span> {JSON.stringify(value)}</li>
}

export const Metadata = ({ metadata }: { metadata: any }) => {
    if (!metadata) return null
    return (
        <div className="text-sm text-gray-600 mt-2">
            <ul className="list-none space-y-1">
                {Object.entries(metadata).filter(([key, value]) => ![null, undefined].includes(value as any)).map(([key, value]) => (
                    <MetadataItem key={key} item={key} value={value} />
                ))}
            </ul>
        </div>
    )
}

export const PDFResult = ({ filename, content, tags, metadata }: SearchItem) => {
    return (
        <div className="bg-white p-6 rounded-xl shadow-md mb-4">
            <h4 className="text-lg font-semibold text-slate-800 mb-2">{filename || 'Untitled'}</h4>
            <Tag tags={tags} />
            <a href={`${SERVER_URL}/files/${filename}`} className="text-primary hover:underline">View PDF</a>
            <Metadata metadata={metadata} />
            {content && (
                <div className="mt-4">
                    <details>
                        <summary className="cursor-pointer text-sm font-medium text-slate-600">View Source</summary>
                        <div className="prose prose-sm max-w-none mt-3">
                            <ReactMarkdown>{content}</ReactMarkdown>
                        </div>
                    </details>
                </div>
            )}
        </div>
    )
}

export const EmailResult = ({ content, tags, metadata }: SearchItem) => {
    return (
        <div className="bg-white p-6 rounded-xl shadow-md mb-4">
            <h4 className="text-lg font-semibold text-slate-800 mb-2">{metadata?.title || metadata?.subject || 'Untitled'}</h4>
            <Tag tags={tags} />
            <Metadata metadata={metadata} />
            {content && (
                <div className="prose prose-sm max-w-none mt-3">
                    <ReactMarkdown>{content}</ReactMarkdown>
                </div>
            )}
        </div>
    )
}

export const SearchResult = ({ result }: { result: SearchItem }) => {
    if (result.mime_type?.startsWith('image/')) {
        return <ImageResult {...result} />
    }
    if (result.mime_type?.startsWith('text/markdown')) {
        return <MarkdownResult {...result} />
    }
    if (result.mime_type?.startsWith('text/')) {
        return <TextResult {...result} />
    }
    if (result.mime_type?.startsWith('application/pdf')) {
        return <PDFResult {...result} />
    }
    if (result.mime_type?.startsWith('message/rfc822')) {
        return <EmailResult {...result} />
    }
    console.log(result)
    return null
}

export default SearchResult

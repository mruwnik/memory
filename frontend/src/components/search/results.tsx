import ReactMarkdown from 'react-markdown'
import { SERVER_URL } from '@/hooks/useAuth'
import { Badge, DetailList, Disclosure, Row } from '@/components/shared/ui'

export type SearchItem = {
    filename: string
    content: string
    chunks: any[]
    tags: string[]
    mime_type: string
    metadata: any
}

const fileUrl = (filename: string) => `${SERVER_URL}/files/${filename}`

const metadataValue = (key: string, value: unknown) =>
    key === 'url' ? <a href={value as string} className="text-primary hover:underline break-all">{value as string}</a>
        : key === 'filename' ? <a href={fileUrl(value as string)} className="text-primary hover:underline">{value as string}</a>
        : typeof value === 'string' ? value
        : JSON.stringify(value)

export const Metadata = ({ metadata }: { metadata: any }) => {
    if (!metadata) return null
    return <DetailList items={Object.entries(metadata).map(([key, value]): [string, React.ReactNode] =>
        [key, value === null || value === undefined ? null : metadataValue(key, value)])} />
}

const Markdown = ({ children }: { children: string }) => (
    <div className="prose prose-sm max-w-none"><ReactMarkdown>{children}</ReactMarkdown></div>
)

const Chunks = ({ chunks }: { chunks: any[] }) => (
    <Disclosure summary="Relevant sections">
        <ul className="space-y-3">
            {chunks.map(({ preview, score }, i) => (
                <li key={i} className="bg-slate-50 p-3 rounded-lg">
                    <div className="text-xs text-primary-dark font-medium mb-1">Score: {(score || 0).toFixed(3)}</div>
                    <p className="text-sm text-slate-600">{preview}</p>
                </li>
            ))}
        </ul>
    </Disclosure>
)

// How each kind of result is titled and what its body shows
const KINDS: { match: string; title: (r: SearchItem) => string; body: (r: SearchItem, title: string) => React.ReactNode }[] = [
    {
        match: 'image/',
        title: r => r.metadata?.title || r.filename,
        body: (r, title) => <img src={fileUrl(r.filename)} alt={title} className="max-w-full h-auto rounded-lg" />,
    },
    {
        match: 'text/markdown',
        title: r => r.filename || r.metadata?.title || r.metadata?.url,
        body: r => r.content && <Markdown>{r.content}</Markdown>,
    },
    {
        match: 'text/',
        title: r => r.filename || r.metadata?.title || r.metadata?.url,
        body: r => r.content && <p className="text-slate-600 whitespace-pre-wrap">{r.content}</p>,
    },
    {
        match: 'application/pdf',
        title: r => r.filename,
        body: r => <>
            <a href={fileUrl(r.filename)} className="text-primary hover:underline">View PDF</a>
            {r.content && <Disclosure summary="View Source"><Markdown>{r.content}</Markdown></Disclosure>}
        </>,
    },
    {
        match: 'message/rfc822',
        title: r => r.metadata?.title || r.metadata?.subject,
        body: r => r.content && <Markdown>{r.content}</Markdown>,
    },
]

export const SearchResult = ({ result }: { result: SearchItem }) => {
    const kind = KINDS.find(k => result.mime_type?.startsWith(k.match))
    if (!kind) return null
    const title = kind.title(result) || 'Untitled'
    return (
        <Row title={title} badges={result.tags?.map((tag, i) => <Badge key={i}>{tag}</Badge>)}>
            <Metadata metadata={result.metadata} />
            {kind.body(result, title)}
            {result.chunks?.length > 0 && <Chunks chunks={result.chunks} />}
        </Row>
    )
}

export default SearchResult

import { useState, useEffect, useCallback, useRef } from 'react'
import { useBooks, Book } from '@/hooks/useBooks'
import { Badge, EmptyState, ErrorState, List, LoadingState, Notice, Row, Section, buttonClass } from '@/components/shared/ui'

export const BooksPanel = () => {
  const { listBooks, uploadBook } = useBooks()
  const [books, setBooks] = useState<Book[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const loadBooks = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listBooks()
      setBooks(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load books')
    } finally {
      setLoading(false)
    }
  }, [listBooks])

  useEffect(() => { loadBooks() }, [loadBooks])

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return

    setUploading(true)
    setUploadError(null)
    setUploadSuccess(null)

    try {
      for (const file of Array.from(files)) {
        await uploadBook(file)
      }
      setUploadSuccess(`${files.length} book(s) uploaded and queued for processing`)
      loadBooks()
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setUploading(false)
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
    }
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadBooks} />

  return (
    <Section
      title="Books"
      description={`${books.length} books`}
      actions={
        <label className={buttonClass('primary', 'md', uploading ? 'opacity-50 cursor-not-allowed' : '')}>
          {uploading ? 'Uploading...' : 'Upload books'}
          <input ref={fileInputRef} type="file" accept=".epub,.pdf,.mobi,.azw,.azw3" multiple
            onChange={handleUpload} disabled={uploading} className="hidden" />
        </label>
      }
    >
      {uploadError && <Notice tone="error">{uploadError}</Notice>}
      {uploadSuccess && <Notice tone="success">{uploadSuccess}</Notice>}

      {books.length === 0 ? (
        <EmptyState message="No books indexed yet" actionLabel="Upload books" onAction={() => fileInputRef.current?.click()} />
      ) : (
        <List>
          {books.map(book => (
            <Row
              key={book.id}
              title={book.file_path
                ? <a href={`/files/${book.file_path}?download=true`} title="Download book" className="text-primary hover:underline">{book.title}</a>
                : book.title}
              badges={book.tags?.map(tag => <Badge key={tag}>{tag}</Badge>)}
              meta={[
                book.author && `by ${book.author}`,
                book.publisher && `Publisher: ${book.publisher}`,
                book.total_pages && `${book.total_pages} pages`,
                book.section_count > 0 && `${book.section_count} sections`,
                book.language && `Language: ${book.language}`,
              ]}
            />
          ))}
        </List>
      )}
    </Section>
  )
}

export default BooksPanel

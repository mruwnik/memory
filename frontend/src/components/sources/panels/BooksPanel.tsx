import { useState, useEffect, useCallback, useRef } from 'react'
import { useBooks, Book } from '@/hooks/useBooks'
import { EmptyState, LoadingState, ErrorState } from '../shared'
import { styles } from '../styles'

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
    <div className={styles.panel}>
      <div className={styles.panelHeader}>
        <h3 className={styles.panelTitle}>Books</h3>
        <div className="flex items-center gap-3">
          <span className="text-sm text-slate-500">{books.length} books</span>
          <label className={`${styles.btnUpload} ${uploading ? 'opacity-50 cursor-not-allowed' : ''}`}>
            {uploading ? 'Uploading...' : 'Upload Books'}
            <input
              ref={fileInputRef}
              type="file"
              accept=".epub,.pdf,.mobi,.azw,.azw3"
              multiple
              onChange={handleUpload}
              disabled={uploading}
              className="hidden"
            />
          </label>
        </div>
      </div>

      {uploadError && <div className={styles.errorBanner}>{uploadError}</div>}
      {uploadSuccess && <div className={styles.successBanner}>{uploadSuccess}</div>}

      {books.length === 0 ? (
        <EmptyState
          message="No books indexed yet"
          actionLabel="Upload Books"
          onAction={() => fileInputRef.current?.click()}
        />
      ) : (
        <div className={styles.sourceList}>
          {books.map(book => (
            <div key={book.id} className={styles.card}>
              <div className={styles.cardHeader}>
                <div className={styles.cardInfo}>
                  <h4 className={styles.cardTitle}>
                    {book.file_path ? (
                      <a
                        href={`/files/${book.file_path}?download=true`}
                        title="Download book"
                        className="text-primary hover:underline"
                      >
                        {book.title}
                      </a>
                    ) : (
                      book.title
                    )}
                  </h4>
                  {book.author && (
                    <p className={styles.cardSubtitle}>by {book.author}</p>
                  )}
                </div>
              </div>
              <div className="flex flex-wrap gap-3 mt-2 text-xs text-slate-500">
                {book.publisher && <span>Publisher: {book.publisher}</span>}
                {book.total_pages && <span>{book.total_pages} pages</span>}
                {book.section_count > 0 && <span>{book.section_count} sections</span>}
                {book.language && <span>Language: {book.language}</span>}
              </div>
              {book.tags && book.tags.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-2">
                  {book.tags.map(tag => (
                    <span key={tag} className="px-2 py-0.5 bg-slate-100 text-slate-600 rounded text-xs">
                      {tag}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default BooksPanel

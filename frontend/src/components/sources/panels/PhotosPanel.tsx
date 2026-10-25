import { useState, useEffect, useCallback, useRef } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { useSources } from '@/hooks/useSources'
import { Button, ConfirmDialog, EmptyState, ErrorState, LoadingState, Notice, Section, useActionNotice } from '@/components/shared/ui'
import { useSourcesContext } from '../context'
import { FileButton } from '../shared'
import { formatUserDate } from '@/utils/timezones'

interface Photo {
  id: number
  filename: string
  file_path: string | null
  exif_taken_at: string | null
  camera: string | null
  tags: string[]
  mime_type: string | null
}

export const PhotosPanel = () => {
  const { apiCall } = useAuth()
  const { deletePhoto } = useSources()
  const [photos, setPhotos] = useState<Photo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null)
  const [photoToDelete, setPhotoToDelete] = useState<Photo | null>(null)
  const { run, notice } = useActionNotice()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const { refreshCounts } = useSourcesContext()
  const loadPhotos = useCallback(async () => {
    try {
      const response = await apiCall('/photos')
      if (!response.ok) throw new Error('Failed to fetch photos')
      const data = await response.json()
      setPhotos(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load photos')
    } finally {
      setLoading(false)
      refreshCounts()
    }
  }, [apiCall, refreshCounts])

  useEffect(() => { loadPhotos() }, [loadPhotos])

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return

    setUploading(true)
    setUploadError(null)
    setUploadSuccess(null)

    try {
      let successCount = 0
      for (const file of Array.from(files)) {
        const formData = new FormData()
        formData.append('file', file)

        const response = await apiCall('/photos/upload', {
          method: 'POST',
          body: formData,
        })

        if (!response.ok) {
          const data = await response.json()
          throw new Error(data.detail || 'Upload failed')
        }
        successCount++
      }
      setUploadSuccess(`${successCount} photo(s) uploaded successfully`)
      loadPhotos()
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setUploading(false)
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
    }
  }

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return null
    return formatUserDate(dateStr, { month: 'short', day: 'numeric', year: 'numeric' })
  }

  const handleDelete = async () => {
    if (!photoToDelete) return
    await run(async () => {
      await deletePhoto(photoToDelete.id)
      setPhotos(photos.filter(p => p.id !== photoToDelete.id))
    })
    setPhotoToDelete(null)
  }

  if (loading) return <LoadingState />
  if (error) return <ErrorState message={error} onRetry={loadPhotos} />

  return (
    <Section title="Photos" description={`${photos.length} photos`} actions={
          <FileButton inputRef={fileInputRef} label="Upload photos" busy={uploading}
            accept=".jpg,.jpeg,.png,.gif,.webp,.heic,.heif" multiple onChange={handleUpload} />
        }>
      {notice}
      {uploadError && <Notice tone="error">{uploadError}</Notice>}
      {uploadSuccess && <Notice tone="success">{uploadSuccess}</Notice>}

      {photos.length === 0 ? (
        <EmptyState
          message="No photos indexed yet"
          actionLabel="Upload photos"
          onAction={() => fileInputRef.current?.click()}
        />
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-4">
          {photos.map(photo => (
            <div key={photo.id} className="group relative bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm">
              {photo.file_path && (
                <div className="aspect-square bg-slate-100">
                  <img
                    src={`/files/${photo.file_path}`}
                    alt={photo.filename}
                    loading="lazy"
                    className="w-full h-full object-cover"
                  />
                </div>
              )}
              <Button variant="dangerSolid"
                onClick={() => setPhotoToDelete(photo)}
                className="absolute top-1 right-1 group-hover:opacity-100"
                title="Delete photo"
                aria-label="Delete photo"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </Button>
              <div className="p-2">
                <span className="text-xs text-slate-700 truncate block" title={photo.filename}>
                  {photo.filename}
                </span>
                <div className="text-xs text-slate-500 mt-1 space-y-0.5">
                  {photo.exif_taken_at && <div>{formatDate(photo.exif_taken_at)}</div>}
                  {photo.camera && <div className="truncate">{photo.camera}</div>}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {photoToDelete && (
        <ConfirmDialog
          message={`Are you sure you want to delete "${photoToDelete.filename}"?`}
          onConfirm={handleDelete}
          onCancel={() => setPhotoToDelete(null)}
        />
      )}
    </Section>
  )
}

export default PhotosPanel

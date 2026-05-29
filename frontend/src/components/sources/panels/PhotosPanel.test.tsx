import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithUser, screen, waitFor } from '@/test/utils'
import { mockResponse } from '@/test/utils'

const apiCall = vi.fn()
const deletePhoto = vi.fn()

vi.mock('@/hooks/useAuth', () => ({
  useAuth: () => ({ apiCall }),
}))

vi.mock('@/hooks/useSources', () => ({
  useSources: () => ({ deletePhoto }),
}))

import { PhotosPanel } from './PhotosPanel'

const photo = (over: Record<string, unknown> = {}) => ({
  id: 1,
  filename: 'beach.jpg',
  file_path: 'photos/beach.jpg',
  exif_taken_at: '2024-06-01T12:00:00Z',
  camera: 'Pixel 8',
  tags: [],
  mime_type: 'image/jpeg',
  ...over,
})

// apiCall router keyed by endpoint substring
const routePhotos = (photos: unknown[], uploadInit?: { ok: boolean; json?: unknown }) => {
  apiCall.mockImplementation(async (endpoint: string) => {
    if (endpoint === '/photos') return mockResponse({ json: photos })
    if (endpoint === '/photos/upload') {
      return mockResponse(uploadInit ?? { ok: true, json: {} })
    }
    return mockResponse({ json: {} })
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  routePhotos([])
  deletePhoto.mockResolvedValue(undefined)
})

describe('PhotosPanel - load states', () => {
  it('shows loading first', () => {
    apiCall.mockReturnValue(new Promise(() => {}))
    renderWithUser(<PhotosPanel />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('shows empty state with zero count when no photos', async () => {
    renderWithUser(<PhotosPanel />)
    expect(await screen.findByText('No photos indexed yet')).toBeInTheDocument()
    expect(screen.getByText('0 photos')).toBeInTheDocument()
  })

  it('shows error state when fetch is not ok and retries', async () => {
    apiCall.mockResolvedValueOnce(mockResponse({ ok: false, status: 500 }))
    const { user } = renderWithUser(<PhotosPanel />)
    expect(await screen.findByText('Failed to fetch photos')).toBeInTheDocument()

    routePhotos([])
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('No photos indexed yet')).toBeInTheDocument()
  })

  it('renders a populated photo grid with filename, count, camera and date', async () => {
    routePhotos([photo(), photo({ id: 2, filename: 'cat.png', camera: null })])
    renderWithUser(<PhotosPanel />)
    expect(await screen.findByText('beach.jpg')).toBeInTheDocument()
    expect(screen.getByText('cat.png')).toBeInTheDocument()
    expect(screen.getByText('2 photos')).toBeInTheDocument()
    expect(screen.getByText('Pixel 8')).toBeInTheDocument()
  })

  it('omits the image element when file_path is null', async () => {
    routePhotos([photo({ file_path: null })])
    renderWithUser(<PhotosPanel />)
    await screen.findByText('beach.jpg')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
})

describe('PhotosPanel - upload flow', () => {
  it('uploads each selected file and shows a success banner then reloads', async () => {
    const { user } = renderWithUser(<PhotosPanel />)
    await screen.findByText('No photos indexed yet')

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    const f1 = new File(['a'], 'a.jpg', { type: 'image/jpeg' })
    const f2 = new File(['b'], 'b.jpg', { type: 'image/jpeg' })
    await user.upload(input, [f1, f2])

    expect(await screen.findByText('2 photo(s) uploaded successfully')).toBeInTheDocument()
    const uploadCalls = apiCall.mock.calls.filter(c => c[0] === '/photos/upload')
    expect(uploadCalls).toHaveLength(2)
    expect(uploadCalls[0][1]).toMatchObject({ method: 'POST' })
    expect(uploadCalls[0][1].body).toBeInstanceOf(FormData)
  })

  it('shows the server detail message when an upload fails', async () => {
    routePhotos([], { ok: false, json: { detail: 'file too large' } })
    const { user } = renderWithUser(<PhotosPanel />)
    await screen.findByText('No photos indexed yet')

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(input, new File(['a'], 'a.jpg', { type: 'image/jpeg' }))

    expect(await screen.findByText('file too large')).toBeInTheDocument()
  })
})

describe('PhotosPanel - delete flow', () => {
  beforeEach(() => routePhotos([photo()]))

  it('opens confirm dialog and deletes on confirm, removing the photo', async () => {
    const { user } = renderWithUser(<PhotosPanel />)
    await screen.findByText('beach.jpg')

    await user.click(screen.getByRole('button', { name: 'Delete photo' }))
    expect(screen.getByText('Are you sure you want to delete "beach.jpg"?')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(deletePhoto).toHaveBeenCalledWith(1))
    await waitFor(() => expect(screen.queryByText('beach.jpg')).not.toBeInTheDocument())
  })

  it('cancels deletion without calling deletePhoto', async () => {
    const { user } = renderWithUser(<PhotosPanel />)
    await screen.findByText('beach.jpg')

    await user.click(screen.getByRole('button', { name: 'Delete photo' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(deletePhoto).not.toHaveBeenCalled()
    expect(screen.getByText('beach.jpg')).toBeInTheDocument()
  })

  it('shows an error and closes the dialog when delete fails', async () => {
    deletePhoto.mockRejectedValueOnce(new Error('delete blew up'))
    const { user } = renderWithUser(<PhotosPanel />)
    await screen.findByText('beach.jpg')

    await user.click(screen.getByRole('button', { name: 'Delete photo' }))
    await user.click(screen.getByRole('button', { name: 'Confirm' }))

    expect(await screen.findByText('delete blew up')).toBeInTheDocument()
  })
})

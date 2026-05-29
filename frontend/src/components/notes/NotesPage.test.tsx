import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor, within } from '@/test/utils'
import userEvent from '@testing-library/user-event'
import { NotesPage } from './NotesPage'

const listNotes = vi.fn()
const fetchFile = vi.fn()
const saveNote = vi.fn()

vi.mock('@/hooks/useMCP', () => ({
  useMCP: () => ({ listNotes, fetchFile, saveNote }),
}))

const fileResult = (text: string) => [{ content: [{ type: 'text', mime_type: 'text/markdown', data: text }] }]

beforeEach(() => {
  listNotes.mockReset().mockResolvedValue([[]])
  fetchFile.mockReset().mockResolvedValue(fileResult('# Hello\n\nbody'))
  saveNote.mockReset().mockResolvedValue(undefined)
})

describe('NotesPage', () => {
  it('shows loading then the empty state', async () => {
    renderWithRouter(<NotesPage />)
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('No notes found')).toBeInTheDocument())
  })

  it('renders an error state when listing fails', async () => {
    listNotes.mockRejectedValue(new Error('mcp down'))
    renderWithRouter(<NotesPage />)
    await waitFor(() => expect(screen.getByText('mcp down')).toBeInTheDocument())
  })

  it('builds a folder tree and lists files, stripping the /notes/ prefix', async () => {
    listNotes.mockResolvedValue([['/notes/work/todo.md', '/notes/readme.md']])
    renderWithRouter(<NotesPage />)
    await waitFor(() => expect(screen.getByText('readme.md')).toBeInTheDocument())
    expect(screen.getByText('work')).toBeInTheDocument()
    // Folder is collapsed initially, so its child is hidden.
    expect(screen.queryByText('todo.md')).not.toBeInTheDocument()
  })

  it('expands and collapses a folder', async () => {
    listNotes.mockResolvedValue([['/notes/work/todo.md']])
    const user = userEvent.setup()
    renderWithRouter(<NotesPage />)
    await waitFor(() => expect(screen.getByText('work')).toBeInTheDocument())
    await user.click(screen.getByText('work'))
    expect(screen.getByText('todo.md')).toBeInTheDocument()
    await user.click(screen.getByText('work'))
    expect(screen.queryByText('todo.md')).not.toBeInTheDocument()
  })

  it('shows the placeholder when no file is selected', async () => {
    listNotes.mockResolvedValue([['/notes/readme.md']])
    renderWithRouter(<NotesPage />)
    await waitFor(() => expect(screen.getByText('Select a file to view its content')).toBeInTheDocument())
  })

  it('selects a file, loads its content, and renders markdown', async () => {
    listNotes.mockResolvedValue([['/notes/readme.md']])
    const user = userEvent.setup()
    renderWithRouter(<NotesPage />)
    await waitFor(() => expect(screen.getByText('readme.md')).toBeInTheDocument())
    await user.click(screen.getByText('readme.md'))
    await waitFor(() => expect(fetchFile).toHaveBeenCalledWith('/notes/readme.md'))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Hello' })).toBeInTheDocument())
  })

  it('switches to edit mode, edits content, and enables Save', async () => {
    listNotes.mockResolvedValue([['/notes/readme.md']])
    const user = userEvent.setup()
    renderWithRouter(<NotesPage />)
    await waitFor(() => expect(screen.getByText('readme.md')).toBeInTheDocument())
    await user.click(screen.getByText('readme.md'))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const textarea = await screen.findByRole('textbox')
    await user.type(textarea, ' extra')
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled()
  })

  it('saves edits via saveNote', async () => {
    listNotes.mockResolvedValue([['/notes/readme.md']])
    const user = userEvent.setup()
    renderWithRouter(<NotesPage />)
    await waitFor(() => expect(screen.getByText('readme.md')).toBeInTheDocument())
    await user.click(screen.getByText('readme.md'))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const textarea = await screen.findByRole('textbox')
    await user.type(textarea, '!')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(saveNote).toHaveBeenCalled())
    expect(saveNote.mock.calls[0][0]).toBe('readme.md')
  })

  it('auto-selects and loads a file from the ?file= URL param', async () => {
    listNotes.mockResolvedValue([['/notes/readme.md']])
    renderWithRouter(<NotesPage />, { initialEntries: ['/ui/notes?file=readme.md'] })
    await waitFor(() => expect(fetchFile).toHaveBeenCalledWith('/notes/readme.md'))
  })

  it('shows a loading-content message while fetching the file body', async () => {
    listNotes.mockResolvedValue([['/notes/readme.md']])
    let resolve!: (v: unknown) => void
    fetchFile.mockReturnValue(new Promise((r) => { resolve = r }))
    const user = userEvent.setup()
    renderWithRouter(<NotesPage />)
    await waitFor(() => expect(screen.getByText('readme.md')).toBeInTheDocument())
    await user.click(screen.getByText('readme.md'))
    expect(await screen.findByText('Loading content...')).toBeInTheDocument()
    resolve(fileResult('done'))
  })
})

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { Link, Route, Routes } from 'react-router-dom'
import { fireEvent, renderWithRouter, screen, waitFor, within } from '@/test/utils'
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

  it('renders an error state with retry when listing fails', async () => {
    listNotes.mockRejectedValueOnce(new Error('mcp down')).mockResolvedValue([['/notes/readme.md']])
    const user = userEvent.setup()
    renderWithRouter(<NotesPage />)
    await user.click(await screen.findByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('readme.md')).toBeInTheDocument()
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
    expect(screen.getByRole('button', { name: 'work' })).toHaveAttribute('aria-expanded', 'true')
    await user.click(screen.getByText('work'))
    expect(screen.queryByText('todo.md')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'work' })).toHaveAttribute('aria-expanded', 'false')
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

  it.each([
    ['going back to the list', () => screen.getByRole('button', { name: 'Files' })],
    ['selecting another file', () => screen.getByText('other.md')],
  ])('asks before discarding unsaved edits when %s', async (_, target) => {
    listNotes.mockResolvedValue([['/notes/readme.md', '/notes/other.md']])
    const user = userEvent.setup()
    renderWithRouter(<NotesPage />)
    await user.click(await screen.findByText('readme.md'))
    await user.click(await screen.findByRole('button', { name: 'Edit' }))
    await user.type(await screen.findByRole('textbox'), '!')

    await user.click(target())
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('textbox')).toHaveValue('# Hello\n\nbody!')

    await user.click(target())
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Discard' }))
    await waitFor(() => expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument())
  })

  it('asks before a URL change replaces unsaved edits, and restores the URL on cancel', async () => {
    listNotes.mockResolvedValue([['/notes/readme.md', '/notes/other.md']])
    const user = userEvent.setup()
    renderWithRouter(<><NotesPage /><Link to="?file=other.md">go other</Link></>, { initialEntries: ['/ui/notes?file=readme.md'] })
    await user.click(await screen.findByRole('button', { name: 'Edit' }))
    await user.type(await screen.findByRole('textbox'), '!')

    await user.click(screen.getByText('go other'))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    expect(screen.getByRole('textbox')).toHaveValue('# Hello\n\nbody!')
    expect(fetchFile).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'readme.md' })).toHaveAttribute('aria-current', 'true')

    await user.click(screen.getByText('go other'))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Discard' }))
    await waitFor(() => expect(fetchFile).toHaveBeenLastCalledWith('/notes/other.md'))
  })

  it('asks before an in-app link leaves the page with unsaved edits', async () => {
    listNotes.mockResolvedValue([['/notes/readme.md']])
    const user = userEvent.setup()
    renderWithRouter(
      <><Routes><Route path="/ui/notes" element={<NotesPage />} /><Route path="/ui/elsewhere" element={<p>Elsewhere</p>} /></Routes><Link to="/ui/elsewhere">leave</Link></>,
      { initialEntries: ['/ui/notes?file=readme.md'] },
    )
    await user.click(await screen.findByRole('button', { name: 'Edit' }))
    await user.type(await screen.findByRole('textbox'), '!')

    await user.click(screen.getByText('leave'))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByText('Elsewhere')).not.toBeInTheDocument()
    expect(screen.getByRole('textbox')).toHaveValue('# Hello\n\nbody!')

    await user.click(screen.getByText('leave'))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Discard' }))
    expect(await screen.findByText('Elsewhere')).toBeInTheDocument()
  })

  it('warns before the tab closes only while there are unsaved edits', async () => {
    listNotes.mockResolvedValue([['/notes/readme.md']])
    const user = userEvent.setup()
    renderWithRouter(<NotesPage />, { initialEntries: ['/ui/notes?file=readme.md'] })
    await user.click(await screen.findByRole('button', { name: 'Edit' }))
    expect(fireEvent(window, new Event('beforeunload', { cancelable: true }))).toBe(true)
    await user.type(await screen.findByRole('textbox'), '!')
    expect(fireEvent(window, new Event('beforeunload', { cancelable: true }))).toBe(false)
  })

  it('shows a load failure with retry and offers no editing until the note loads', async () => {
    listNotes.mockResolvedValue([['/notes/readme.md']])
    fetchFile.mockRejectedValueOnce(new Error('fetch broke'))
    const user = userEvent.setup()
    renderWithRouter(<NotesPage />)
    await user.click(await screen.findByText('readme.md'))
    expect(await screen.findByText('fetch broke')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('heading', { name: 'Hello' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument()
  })

  it('shows a save failure inline and keeps the file tree and edits', async () => {
    listNotes.mockResolvedValue([['/notes/readme.md']])
    saveNote.mockRejectedValueOnce(new Error('disk full'))
    const user = userEvent.setup()
    renderWithRouter(<NotesPage />)
    await user.click(await screen.findByText('readme.md'))
    await user.click(await screen.findByRole('button', { name: 'Edit' }))
    await user.type(await screen.findByRole('textbox'), '!')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('disk full')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /readme\.md/ })).toBeInTheDocument()
    expect(screen.getByRole('textbox')).toHaveValue('# Hello\n\nbody!')
  })

  it('shows the last clicked note when an earlier load answers late', async () => {
    listNotes.mockResolvedValue([['/notes/a.md', '/notes/b.md']])
    let answerA: (value: unknown) => void = () => {}
    fetchFile.mockImplementation((path: string) => path === '/notes/a.md'
      ? new Promise(resolve => { answerA = resolve })
      : Promise.resolve(fileResult('B text')))
    const user = userEvent.setup()
    renderWithRouter(<NotesPage />)
    await user.click(await screen.findByText('a.md'))
    await user.click(screen.getByText('b.md'))
    expect(await screen.findByText('B text')).toBeInTheDocument()
    answerA(fileResult('A text'))
    await new Promise(r => setTimeout(r, 0))
    expect(fetchFile.mock.calls).toEqual([['/notes/a.md'], ['/notes/b.md']])
    expect(screen.queryByText('A text')).not.toBeInTheDocument()
    expect(screen.getByText('B text')).toBeInTheDocument()
  })

  it('drops a save failure notice when another note is opened', async () => {
    listNotes.mockResolvedValue([['/notes/a.md', '/notes/b.md']])
    saveNote.mockRejectedValueOnce(new Error('disk full'))
    const user = userEvent.setup()
    renderWithRouter(<NotesPage />)
    await user.click(await screen.findByText('a.md'))
    await user.click(await screen.findByRole('button', { name: 'Edit' }))
    await user.type(await screen.findByRole('textbox'), '!')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await screen.findByText('disk full')
    await user.click(screen.getByText('b.md'))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Discard' }))
    await waitFor(() => expect(fetchFile).toHaveBeenCalledWith('/notes/b.md'))
    expect(screen.queryByText('disk full')).not.toBeInTheDocument()
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

describe('NotesPage new note', () => {
  const openModal = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(await screen.findByRole('button', { name: 'New note' }))
    return screen.getByRole('dialog')
  }

  it('opens an empty editor for the new path and saves it with the title', async () => {
    listNotes.mockResolvedValue([['/notes/readme.md']])
    const user = userEvent.setup()
    renderWithRouter(<NotesPage />)
    const dialog = await openModal(user)
    await user.type(within(dialog).getByLabelText(/Path/), 'work/ideas')
    await user.type(within(dialog).getByLabelText('Title'), 'Ideas')
    await user.click(within(dialog).getByRole('button', { name: 'Create note' }))

    expect(fetchFile).not.toHaveBeenCalled()
    expect(screen.getByText('work/ideas.md')).toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: 'Note content' }), 'hello')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(saveNote).toHaveBeenCalledWith('work/ideas.md', 'hello', 'Ideas', true))
    // The note is listed right away, before the background write is visible to a reload.
    expect(await screen.findByText('ideas.md')).toBeInTheDocument()
  })

  it('re-checks a fresh listing so a note created elsewhere is not overwritten', async () => {
    listNotes.mockResolvedValueOnce([['/notes/readme.md']]).mockResolvedValue([['/notes/readme.md', '/notes/other.md']])
    const user = userEvent.setup()
    renderWithRouter(<NotesPage />)
    const dialog = await openModal(user)
    await user.type(within(dialog).getByLabelText(/Path/), 'other')
    await user.click(within(dialog).getByRole('button', { name: 'Create note' }))
    expect(await within(dialog).findByText('other.md already exists.')).toBeInTheDocument()
  })

  it.each([
    ['/', 'Enter a file name.'],
    ['a/../b', 'The path can\'t contain empty, "." or ".." parts.'],
    ['readme', 'readme.md already exists.'],
  ])('rejects the path %j inline', async (path, message) => {
    listNotes.mockResolvedValue([['/notes/readme.md']])
    const user = userEvent.setup()
    renderWithRouter(<NotesPage />)
    const dialog = await openModal(user)
    await user.type(within(dialog).getByLabelText(/Path/), path)
    await user.click(within(dialog).getByRole('button', { name: 'Create note' }))
    expect(within(dialog).getByText(message)).toBeInTheDocument()
    expect(saveNote).not.toHaveBeenCalled()
  })

  it('asks before discarding unsaved edits to open the new note', async () => {
    listNotes.mockResolvedValue([['/notes/readme.md']])
    const user = userEvent.setup()
    renderWithRouter(<NotesPage />)
    await user.click(await screen.findByText('readme.md'))
    await user.click(await screen.findByRole('button', { name: 'Edit' }))
    await user.type(await screen.findByRole('textbox'), '!')

    const dialog = await openModal(user)
    await user.clear(within(dialog).getByLabelText(/Path/))
    await user.type(within(dialog).getByLabelText(/Path/), 'fresh')
    await user.click(within(dialog).getByRole('button', { name: 'Create note' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Discard' }))
    expect(await screen.findByText('fresh.md')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Note content' })).toHaveValue('')
  })
})

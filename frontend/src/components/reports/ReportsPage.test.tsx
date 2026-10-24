import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderWithRouter, screen, waitFor, within } from '@/test/utils'
import userEvent from '@testing-library/user-event'
import { mockFetch, mockResponse } from '@/test/utils'
import type { Report } from '@/hooks/useReports'
import { ReportsPage } from './index'

const listReports = vi.fn()
const createReport = vi.fn()
const uploadReport = vi.fn()
const deleteReport = vi.fn()

vi.mock('@/hooks/useReports', () => ({
  useReports: () => ({ listReports, createReport, uploadReport, deleteReport }),
}))

const makeReport = (o: Partial<Report> = {}): Report => ({
  id: 1,
  title: 'Quarterly',
  filename: 'reports/quarterly.html',
  tags: ['finance'],
  inserted_at: new Date().toISOString(),
  modality: 'report',
  mime_type: 'text/html',
  size: 100,
  preview: null,
  metadata: { report_format: 'html', allow_scripts: false },
  ...o,
})

beforeEach(() => {
  listReports.mockReset().mockResolvedValue([])
  createReport.mockReset().mockResolvedValue(undefined)
  uploadReport.mockReset().mockResolvedValue(undefined)
  deleteReport.mockReset().mockResolvedValue(undefined)
  vi.useRealTimers()
})

describe('ReportsPage', () => {
  it('shows loading then the empty state', async () => {
    renderWithRouter(<ReportsPage />)
    await waitFor(() => expect(screen.getByText('No reports yet.')).toBeInTheDocument())
  })

  it('renders the error state with retry', async () => {
    listReports.mockRejectedValueOnce(new Error('reports down'))
    renderWithRouter(<ReportsPage />)
    await waitFor(() => expect(screen.getByText('reports down')).toBeInTheDocument())
  })

  it('lists reports with format badges and count', async () => {
    listReports.mockResolvedValue([
      makeReport({ id: 1, title: 'HTML one', metadata: { report_format: 'html' } }),
      makeReport({ id: 2, title: 'PDF one', filename: 'reports/r.pdf', mime_type: 'application/pdf', metadata: { report_format: 'pdf' } }),
    ])
    renderWithRouter(<ReportsPage />)
    await waitFor(() => expect(screen.getByText('2 reports')).toBeInTheDocument())
    expect(screen.getByText('HTML one')).toBeInTheDocument()
    expect(screen.getByText('PDF')).toBeInTheDocument()
  })

  it('toggles the create form open and closed', async () => {
    const user = userEvent.setup()
    renderWithRouter(<ReportsPage />)
    await waitFor(() => expect(screen.getByText('No reports yet.')).toBeInTheDocument())
    await user.click(screen.getAllByRole('button', { name: 'New report' })[0])
    expect(screen.getByText('Write HTML')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByText('Write HTML')).not.toBeInTheDocument()
  })

  it('validates required title and content for HTML reports', async () => {
    const user = userEvent.setup()
    renderWithRouter(<ReportsPage />)
    await waitFor(() => expect(screen.getByText('No reports yet.')).toBeInTheDocument())
    await user.click(screen.getAllByRole('button', { name: 'New report' })[0])
    const dialog = screen.getByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Create report' }))
    expect(within(dialog).getByLabelText('Title')).toBeInvalid()
    expect(within(dialog).getByLabelText('HTML content')).toBeInvalid()
    expect(createReport).not.toHaveBeenCalled()
  })

  it('creates an HTML report with title, content and tags', async () => {
    const user = userEvent.setup()
    renderWithRouter(<ReportsPage />)
    await waitFor(() => expect(screen.getByText('No reports yet.')).toBeInTheDocument())
    await user.click(screen.getAllByRole('button', { name: 'New report' })[0])
    await user.type(screen.getByPlaceholderText('Report title'), 'My Report')
    await user.type(screen.getByPlaceholderText(/My Report/), '<h1>Hi</h1>')
    const submitBtn = within(screen.getByRole('dialog')).getByRole('button', { name: 'Create report' })
    await user.click(submitBtn)
    await waitFor(() => expect(createReport).toHaveBeenCalled())
    expect(createReport.mock.calls[0][0]).toBe('My Report')
    expect(createReport.mock.calls[0][1]).toContain('<h1>Hi</h1>')
    await waitFor(() => expect(screen.getByText('Report queued for processing...')).toBeInTheDocument())
  })

  it('keeps polling after a create until the new report shows up', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    listReports.mockResolvedValueOnce([]).mockResolvedValueOnce([]).mockResolvedValueOnce([])
      .mockResolvedValue([makeReport({ id: 3, title: 'Late report' })])
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderWithRouter(<ReportsPage />)
    await waitFor(() => expect(screen.getByText('No reports yet.')).toBeInTheDocument())
    await user.click(screen.getAllByRole('button', { name: 'New report' })[0])
    await user.type(screen.getByPlaceholderText('Report title'), 'Late report')
    await user.type(screen.getByPlaceholderText(/My Report/), '<p>x</p>')
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Create report' }))
    await vi.advanceTimersByTimeAsync(1000)
    expect(screen.getByText('Report queued for processing...')).toBeInTheDocument()
    await vi.advanceTimersByTimeAsync(4000)
    expect(await screen.findByText('Late report')).toBeInTheDocument()
    expect(screen.queryByText('Report queued for processing...')).not.toBeInTheDocument()
  })

  it.each([
    [false, ''],
    [true, 'allow-scripts'],
  ])('renders the HTML iframe viewer without same-origin (allow_scripts=%s)', async (allowScripts, sandbox) => {
    listReports.mockResolvedValue([
      makeReport({ id: 5, title: 'Viewable', metadata: { report_format: 'html', allow_scripts: allowScripts } }),
    ])
    const user = userEvent.setup()
    renderWithRouter(<ReportsPage />)
    await waitFor(() => expect(screen.getByText('Viewable')).toBeInTheDocument())
    await user.click(screen.getByText('Viewable'))
    const frame = await screen.findByTitle('Viewable')
    expect(frame).toHaveAttribute('src', '/reports/quarterly.html')
    expect(frame).toHaveAttribute('sandbox', sandbox)
  })

  it('shows an Open PDF action for PDF reports', async () => {
    listReports.mockResolvedValue([
      makeReport({ id: 6, title: 'PDF report', filename: 'reports/r.pdf', mime_type: 'application/pdf', metadata: { report_format: 'pdf' } }),
    ])
    const user = userEvent.setup()
    renderWithRouter(<ReportsPage />)
    await waitFor(() => expect(screen.getByText('PDF report')).toBeInTheDocument())
    await user.click(screen.getByText('PDF report'))
    await waitFor(() => expect(screen.getByRole('link', { name: 'Open PDF' })).toBeInTheDocument())
  })

  it('deletes a report after confirming', async () => {
    listReports.mockResolvedValue([makeReport({ id: 9, title: 'Delete me' })])
    const user = userEvent.setup()
    renderWithRouter(<ReportsPage />)
    await waitFor(() => expect(screen.getByText('Delete me')).toBeInTheDocument())
    await user.click(screen.getByText('Delete me'))
    await user.click(await screen.findByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(deleteReport).toHaveBeenCalledWith(9))
  })

  it('shows a delete failure in the detail pane and keeps the list', async () => {
    deleteReport.mockRejectedValueOnce(new Error('delete broke'))
    listReports.mockResolvedValue([makeReport({ id: 9, title: 'Delete me' })])
    const user = userEvent.setup()
    renderWithRouter(<ReportsPage />)
    await user.click(await screen.findByText('Delete me'))
    await user.click(await screen.findByRole('button', { name: 'Delete' }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete' }))
    expect(await screen.findByText('delete broke')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Delete me/ })).toBeInTheDocument()
  })

  it('edits an HTML report: fetches current content and re-creates with metadata', async () => {
    const fetchMock = mockFetch(async () => mockResponse({ text: '<h1>old</h1>', status: 200 }))
    listReports.mockResolvedValue([makeReport({ id: 11, title: 'Editable', filename: 'reports/e.html' })])
    const user = userEvent.setup()
    renderWithRouter(<ReportsPage />)
    await waitFor(() => expect(screen.getByText('Editable')).toBeInTheDocument())
    await user.click(screen.getByText('Editable'))
    await user.click(await screen.findByRole('button', { name: 'Edit' }))
    await user.type(within(screen.getByRole('dialog')).getByLabelText('Title'), ' v2')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(createReport).toHaveBeenCalled())
    expect(fetchMock).toHaveBeenCalledWith('/reports/e.html')
    expect(createReport.mock.calls.at(-1)?.[0]).toBe('Editable v2')
    // 4th positional arg: the existing report's name within the reports directory
    expect(createReport.mock.calls.at(-1)?.[3]).toBe('e.html')
  })

  it('closes an edit that changes nothing without re-saving or waiting', async () => {
    listReports.mockResolvedValue([makeReport({ id: 11, title: 'Editable', filename: 'reports/e.html' })])
    const user = userEvent.setup()
    renderWithRouter(<ReportsPage />)
    await user.click(await screen.findByText('Editable'))
    await user.click(await screen.findByRole('button', { name: 'Edit' }))
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(createReport).not.toHaveBeenCalled()
    expect(screen.queryByText(/queued|processing/i)).not.toBeInTheDocument()
  })

  it('stops polling when the page unmounts mid-request', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let release: (value: Report[]) => void = () => {}
    listReports.mockResolvedValueOnce([])
      .mockImplementationOnce(() => new Promise<Report[]>(resolve => { release = resolve }))
      .mockResolvedValue([])
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const { unmount } = renderWithRouter(<ReportsPage />)
    await screen.findByText('No reports yet.')
    await user.click(screen.getAllByRole('button', { name: 'New report' })[0])
    await user.type(screen.getByPlaceholderText('Report title'), 'R')
    await user.type(screen.getByPlaceholderText(/My Report/), '<p>x</p>')
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Create report' }))
    await vi.advanceTimersByTimeAsync(1000)
    expect(listReports).toHaveBeenCalledTimes(2)

    unmount()
    release([])
    await vi.advanceTimersByTimeAsync(30_000)
    expect(listReports).toHaveBeenCalledTimes(2)
  })
})

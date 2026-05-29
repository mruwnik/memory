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
  filename: 'quarterly.html',
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
      makeReport({ id: 2, title: 'PDF one', filename: 'r.pdf', mime_type: 'application/pdf', metadata: { report_format: 'pdf' } }),
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
    await user.click(screen.getByRole('button', { name: 'New Report' }))
    expect(screen.getByText('Write HTML')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByText('Write HTML')).not.toBeInTheDocument()
  })

  it('validates required title and content for HTML reports', async () => {
    const user = userEvent.setup()
    renderWithRouter(<ReportsPage />)
    await waitFor(() => expect(screen.getByText('No reports yet.')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'New Report' }))
    const submitBtn = screen
      .getAllByRole('button', { name: 'Create Report' })
      .find((b) => (b as HTMLButtonElement).type === 'submit')!
    await user.click(submitBtn)
    expect(await screen.findByText('Title and content are required')).toBeInTheDocument()
    expect(createReport).not.toHaveBeenCalled()
  })

  it('creates an HTML report with title, content and tags', async () => {
    const user = userEvent.setup()
    renderWithRouter(<ReportsPage />)
    await waitFor(() => expect(screen.getByText('No reports yet.')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'New Report' }))
    await user.type(screen.getByPlaceholderText('Report title'), 'My Report')
    await user.type(screen.getByPlaceholderText(/My Report/), '<h1>Hi</h1>')
    const submitBtn = screen
      .getAllByRole('button', { name: 'Create Report' })
      .find((b) => (b as HTMLButtonElement).type === 'submit')!
    await user.click(submitBtn)
    await waitFor(() => expect(createReport).toHaveBeenCalled())
    expect(createReport.mock.calls[0][0]).toBe('My Report')
    expect(createReport.mock.calls[0][1]).toContain('<h1>Hi</h1>')
    await waitFor(() => expect(screen.getByText('Report queued for processing...')).toBeInTheDocument())
  })

  it('selects a report and renders the HTML iframe viewer', async () => {
    listReports.mockResolvedValue([makeReport({ id: 5, title: 'Viewable' })])
    const user = userEvent.setup()
    renderWithRouter(<ReportsPage />)
    await waitFor(() => expect(screen.getByText('Viewable')).toBeInTheDocument())
    await user.click(screen.getByText('Viewable'))
    await waitFor(() =>
      expect(screen.getByTitle('Viewable')).toHaveAttribute('src', '/reports/quarterly.html'),
    )
  })

  it('shows an Open PDF action for PDF reports', async () => {
    listReports.mockResolvedValue([
      makeReport({ id: 6, title: 'PDF report', filename: 'r.pdf', mime_type: 'application/pdf', metadata: { report_format: 'pdf' } }),
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
    await user.click(await screen.findByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(deleteReport).toHaveBeenCalledWith(9))
  })

  it('edits an HTML report: fetches current content and re-creates with metadata', async () => {
    mockFetch(async () => mockResponse({ text: '<h1>old</h1>', status: 200 }))
    listReports.mockResolvedValue([makeReport({ id: 11, title: 'Editable', filename: 'e.html' })])
    const user = userEvent.setup()
    renderWithRouter(<ReportsPage />)
    await waitFor(() => expect(screen.getByText('Editable')).toBeInTheDocument())
    await user.click(screen.getByText('Editable'))
    await user.click(await screen.findByRole('button', { name: 'Edit' }))
    await user.click(screen.getByRole('button', { name: 'Save Changes' }))
    await waitFor(() => expect(createReport).toHaveBeenCalled())
    // 4th positional arg is the existing filename for upsert
    expect(createReport.mock.calls.at(-1)?.[3]).toBe('e.html')
  })
})

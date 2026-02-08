import { useState, useEffect, useCallback, useRef } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useReports, type Report } from '@/hooks/useReports'
import { styles, cx } from '../sources/styles'
import { LoadingState, ErrorState, EmptyState, TagsInput, ConfirmDialog } from '../sources/shared'
import { formatRelativeTime } from '../sources/shared'

type FormMode = 'html' | 'upload'

const ReportsPage = () => {
  const { listReports, createReport, uploadReport, deleteReport } = useReports()
  const [searchParams, setSearchParams] = useSearchParams()

  const [reports, setReports] = useState<Report[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [formMode, setFormMode] = useState<FormMode>('html')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  // Form fields
  const [title, setTitle] = useState('')
  const [htmlContent, setHtmlContent] = useState('')
  const [tags, setTags] = useState<string[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [queued, setQueued] = useState(false)
  const [deletingReport, setDeletingReport] = useState<Report | null>(null)
  const [deleting, setDeleting] = useState(false)
  const refreshTimerRef = useRef<number>()

  // Cleanup refresh timer on unmount
  useEffect(() => {
    return () => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
    }
  }, [])

  const selectedId = searchParams.get('id')

  const selectedReport = reports.find(r => String(r.id) === selectedId) || null

  const loadReports = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listReports()
      setReports(Array.isArray(data) ? data : [])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load reports')
    } finally {
      setLoading(false)
    }
  }, [listReports])

  useEffect(() => {
    loadReports()
  }, [loadReports])

  const resetForm = () => {
    setTitle('')
    setHtmlContent('')
    setTags([])
    setFile(null)
    setFormError(null)
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setFormError(null)
    try {
      if (formMode === 'html') {
        if (!title.trim() || !htmlContent.trim()) {
          setFormError('Title and content are required')
          return
        }
        await createReport(title.trim(), htmlContent, tags.length ? tags : undefined)
      } else {
        if (!file) {
          setFormError('Please select a file')
          return
        }
        await uploadReport(file, title.trim() || undefined, tags.length ? tags.join(',') : undefined)
      }
      setShowForm(false)
      resetForm()
      setQueued(true)
      // Brief delay for backend processing before refresh, with cleanup
      refreshTimerRef.current = window.setTimeout(() => {
        loadReports().finally(() => setQueued(false))
      }, 1000)
    } catch (e) {
      setFormError(e instanceof Error ? e.message : 'Operation failed')
    } finally {
      setSubmitting(false)
    }
  }

  const handleSelectReport = (report: Report) => {
    setSearchParams({ id: String(report.id) })
  }

  const handleDelete = async () => {
    if (!deletingReport) return
    setDeleting(true)
    try {
      await deleteReport(deletingReport.id)
      if (String(deletingReport.id) === selectedId) {
        setSearchParams({})
      }
      setDeletingReport(null)
      await loadReports()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to delete report')
    } finally {
      setDeleting(false)
    }
  }

  const reportFormat = (report: Report): string =>
    report.metadata?.report_format || (report.mime_type?.includes('pdf') ? 'pdf' : 'html')

  const reportTitle = (report: Report): string =>
    report.metadata?.report_title || report.title || report.filename || `Report #${report.id}`

  const reportUrl = (report: Report): string | null => {
    if (!report.filename) return null
    return `/reports/${report.filename}`
  }

  return (
    <div className="min-h-screen flex flex-col bg-slate-50">
      <header className="bg-white border-b border-slate-200 px-8 py-4 flex justify-between items-center shadow-sm">
        <div className="flex items-center gap-4">
          <Link
            to="/ui/dashboard"
            className="text-slate-500 hover:text-slate-700 transition-colors"
          >
            &larr; Back
          </Link>
          <h1 className="text-primary text-2xl font-semibold">Reports</h1>
        </div>
        <button
          className={styles.btnAdd}
          onClick={() => { setShowForm(!showForm); resetForm() }}
        >
          {showForm ? 'Cancel' : 'New Report'}
        </button>
      </header>

      <main className="flex-1 flex overflow-hidden">
        {/* Left panel — Report list */}
        <div className="w-80 bg-white border-r border-slate-200 flex flex-col">
          {showForm && (
            <div className="border-b border-slate-200 p-4">
              <form onSubmit={handleSubmit} className={styles.form}>
                {/* Mode toggle */}
                <div className="flex rounded-md border border-slate-200 overflow-hidden mb-3">
                  <button
                    type="button"
                    onClick={() => setFormMode('html')}
                    className={cx(
                      'flex-1 px-3 py-1.5 text-sm font-medium transition-colors',
                      formMode === 'html' ? 'bg-slate-100 text-slate-700' : 'bg-white text-slate-500 hover:bg-slate-50'
                    )}
                  >
                    Write HTML
                  </button>
                  <button
                    type="button"
                    onClick={() => setFormMode('upload')}
                    className={cx(
                      'flex-1 px-3 py-1.5 text-sm font-medium transition-colors border-l border-slate-200',
                      formMode === 'upload' ? 'bg-slate-100 text-slate-700' : 'bg-white text-slate-500 hover:bg-slate-50'
                    )}
                  >
                    Upload File
                  </button>
                </div>

                <div className={styles.formGroup}>
                  <label className={styles.formLabel}>Title</label>
                  <input
                    type="text"
                    value={title}
                    onChange={e => setTitle(e.target.value)}
                    className={styles.formInput}
                    placeholder="Report title"
                  />
                </div>

                {formMode === 'html' ? (
                  <div className={styles.formGroup}>
                    <label className={styles.formLabel}>HTML Content</label>
                    <textarea
                      value={htmlContent}
                      onChange={e => setHtmlContent(e.target.value)}
                      className={cx(styles.formTextarea, 'h-32 font-mono text-xs')}
                      placeholder="<h1>My Report</h1>&#10;<p>Content here...</p>"
                    />
                  </div>
                ) : (
                  <div className={styles.formGroup}>
                    <label className={styles.formLabel}>File (.html, .htm, .pdf)</label>
                    <input
                      type="file"
                      accept=".html,.htm,.pdf"
                      onChange={e => setFile(e.target.files?.[0] || null)}
                      className="text-sm text-slate-600"
                    />
                  </div>
                )}

                <div className={styles.formGroup}>
                  <label className={styles.formLabel}>Tags</label>
                  <TagsInput tags={tags} onChange={setTags} />
                </div>

                {formError && <div className={styles.formError}>{formError}</div>}

                <div className={styles.formActions}>
                  <button type="submit" disabled={submitting} className={styles.btnSubmit}>
                    {submitting ? 'Saving...' : formMode === 'html' ? 'Create Report' : 'Upload'}
                  </button>
                </div>
              </form>
            </div>
          )}

          <div className="px-4 py-3 border-b border-slate-200">
            <h2 className="text-sm font-semibold text-slate-700">
              {loading ? 'Loading...' : `${reports.length} report${reports.length !== 1 ? 's' : ''}`}
            </h2>
            {queued && (
              <p className="text-xs text-amber-600 mt-1">Report queued for processing...</p>
            )}
          </div>

          <div className="flex-1 overflow-y-auto">
            {loading ? (
              <LoadingState />
            ) : error ? (
              <ErrorState message={error} onRetry={loadReports} />
            ) : reports.length === 0 ? (
              <EmptyState
                message="No reports yet."
                actionLabel="Create Report"
                onAction={() => setShowForm(true)}
              />
            ) : (
              <div className="divide-y divide-slate-100">
                {reports.map(report => {
                  const isSelected = String(report.id) === selectedId
                  const format = reportFormat(report)
                  return (
                    <button
                      key={report.id}
                      onClick={() => handleSelectReport(report)}
                      className={cx(
                        'w-full text-left px-4 py-3 transition-colors',
                        isSelected ? 'bg-primary/10' : 'hover:bg-slate-50'
                      )}
                    >
                      <div className="flex items-center gap-2">
                        <span className="flex-1 font-medium text-sm text-slate-800 truncate">
                          {reportTitle(report)}
                        </span>
                        <span className={cx(
                          styles.badge,
                          format === 'pdf' ? 'bg-red-100 text-red-700' : 'bg-blue-100 text-blue-700'
                        )}>
                          {format.toUpperCase()}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-xs text-slate-400">
                          {formatRelativeTime(report.inserted_at)}
                        </span>
                        {(report.tags?.length ?? 0) > 0 && (
                          <span className="text-xs text-slate-400 truncate">
                            {report.tags.slice(0, 3).join(', ')}
                          </span>
                        )}
                      </div>
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        </div>

        {/* Right panel — Viewer */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {selectedReport ? (
            <>
              <div className="px-6 py-3 border-b border-slate-200 bg-white flex items-center justify-between">
                <h2 className="text-sm font-semibold text-slate-700 truncate">
                  {reportTitle(selectedReport)}
                </h2>
                <div className="flex items-center gap-2">
                  {reportUrl(selectedReport) && reportFormat(selectedReport) === 'pdf' && (
                    <a
                      href={reportUrl(selectedReport)!}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={styles.btnPrimary}
                    >
                      Open PDF
                    </a>
                  )}
                  <button
                    onClick={() => setDeletingReport(selectedReport)}
                    disabled={deleting}
                    className={styles.btnDanger}
                  >
                    Delete
                  </button>
                </div>
              </div>
              <div className="flex-1 overflow-hidden bg-white">
                {reportUrl(selectedReport) ? (
                  reportFormat(selectedReport) === 'pdf' ? (
                    <div className="flex items-center justify-center h-full text-slate-500">
                      <div className="text-center">
                        <p className="text-lg mb-4">PDF Report</p>
                        <a
                          href={reportUrl(selectedReport)!}
                          target="_blank"
                          rel="noopener noreferrer"
                          className={styles.btnPrimary}
                        >
                          Open in new tab
                        </a>
                      </div>
                    </div>
                  ) : (
                    <iframe
                      src={reportUrl(selectedReport)!}
                      sandbox="allow-same-origin"
                      className="w-full h-full border-none"
                      title={reportTitle(selectedReport)}
                    />
                  )
                ) : (
                  <div className="flex items-center justify-center h-full text-slate-400">
                    Report file not available
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center text-slate-400">
              Select a report to view
            </div>
          )}
        </div>
      </main>

      {deletingReport && (
        <ConfirmDialog
          message={`Are you sure you want to delete "${reportTitle(deletingReport)}"?`}
          onConfirm={handleDelete}
          onCancel={() => setDeletingReport(null)}
        />
      )}
    </div>
  )
}

export default ReportsPage

import { useState, useEffect, useCallback, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useReports, type Report } from '@/hooks/useReports'
import { TagsInput } from '../sources/shared'
import { Badge, Button, Checkbox, ConfirmDialog, EmptyState, ErrorState, Field, FormActions, Input, Label, LoadingState, Notice, Page, Textarea, ToggleGroup, buttonClass, cx } from '@/components/shared/ui'
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
  const [allowScripts, setAllowScripts] = useState(false)
  const [allowedConnectUrls, setAllowedConnectUrls] = useState<string[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [queued, setQueued] = useState(false)
  const [deletingReport, setDeletingReport] = useState<Report | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [editTags, setEditTags] = useState<string[]>([])
  const [editAllowScripts, setEditAllowScripts] = useState(false)
  const [editAllowedConnectUrls, setEditAllowedConnectUrls] = useState<string[]>([])
  const [editSubmitting, setEditSubmitting] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)
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
    setAllowScripts(false)
    setAllowedConnectUrls([])
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
        await createReport(
          title.trim(),
          htmlContent,
          tags.length ? tags : undefined,
          undefined,
          allowScripts || undefined,
          allowedConnectUrls  // Always send the array, even if empty
        )
      } else {
        if (!file) {
          setFormError('Please select a file')
          return
        }
        await uploadReport(
          file,
          title.trim() || undefined,
          tags.length ? tags.join(',') : undefined,
          allowScripts || undefined,
          allowedConnectUrls  // Always send the array, even if empty
        )
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
    setEditing(false)
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

  const startEditing = (report: Report) => {
    setEditing(true)
    setEditTitle(reportTitle(report))
    setEditTags(report.tags || [])
    setEditAllowScripts(report.metadata?.allow_scripts ?? false)
    setEditAllowedConnectUrls(report.metadata?.allowed_connect_urls || [])
    setEditError(null)
  }

  const handleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedReport?.filename) return
    setEditSubmitting(true)
    setEditError(null)
    try {
      // Fetch the current file content so we can upsert with updated metadata
      const url = reportUrl(selectedReport)
      if (!url) throw new Error('Report file not available')
      const resp = await fetch(url)
      if (!resp.ok) throw new Error('Failed to fetch report content')
      const content = await resp.text()
      await createReport(
        editTitle.trim(),
        content,
        editTags.length ? editTags : undefined,
        selectedReport.filename,
        editAllowScripts || undefined,
        editAllowedConnectUrls  // Always send the array, even if empty
      )
      setEditing(false)
      setQueued(true)
      refreshTimerRef.current = window.setTimeout(() => {
        loadReports().finally(() => setQueued(false))
      }, 1000)
    } catch (e) {
      setEditError(e instanceof Error ? e.message : 'Edit failed')
    } finally {
      setEditSubmitting(false)
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
    <Page
      title="Reports"
      width="full"
      fill
      actions={
        <Button variant={showForm ? 'secondary' : 'primary'} onClick={() => { setShowForm(!showForm); resetForm() }}>
          {showForm ? 'Cancel' : 'New Report'}
        </Button>
      }
    >
      <div className="h-full flex overflow-hidden bg-white rounded-xl border border-slate-200 shadow-sm">
        {/* Left panel — Report list */}
        <div className="w-80 shrink-0 border-r border-slate-200 flex flex-col">
          {showForm && (
            <div className="border-b border-slate-200 p-4">
              <form onSubmit={handleSubmit} className="space-y-4">
                {/* Mode toggle */}
                <ToggleGroup label="Report source" value={formMode} onChange={setFormMode}
                  options={[{ value: 'html', label: 'Write HTML' }, { value: 'upload', label: 'Upload File' }]} />

                <Field label="Title">
                  <Input
                    type="text"
                    value={title}
                    onChange={e => setTitle(e.target.value)}
                    placeholder="Report title"
                  />
                </Field>

                {formMode === 'html' ? (
                  <Field label="HTML Content">
                    <Textarea
                      value={htmlContent}
                      onChange={e => setHtmlContent(e.target.value)}
                      className="h-32 font-mono text-xs"
                      placeholder="<h1>My Report</h1>&#10;<p>Content here...</p>"
                    />
                  </Field>
                ) : (
                  <Field>
                    <Label>File (.html, .htm, .pdf)</Label>
                    <input
                      type="file"
                      accept=".html,.htm,.pdf"
                      onChange={e => setFile(e.target.files?.[0] || null)}
                      className="text-sm text-slate-600"
                    />
                  </Field>
                )}

                <Field label="Tags">
                  <TagsInput tags={tags} onChange={setTags} />
                </Field>

                <Checkbox checked={allowScripts} onChange={e => setAllowScripts(e.target.checked)} label="Allow scripts" />

                {allowScripts && (
                  <Field>
                    <Label>
                      Allowed External URLs
                      <span className="text-xs text-slate-500 ml-2">(for CSP connect-src)</span>
                    </Label>
                    <TagsInput
                      tags={allowedConnectUrls}
                      onChange={setAllowedConnectUrls}
                      placeholder="https://example.com/api/"
                    />
                    <p className="text-xs text-slate-500 mt-1">
                      External URLs this report can connect to (e.g., for calling external MCP servers)
                    </p>
                  </Field>
                )}

                {formError && <Notice tone="error">{formError}</Notice>}

                <FormActions>
                  <Button variant="primary" type="submit" disabled={submitting}>
                    {submitting ? 'Saving...' : formMode === 'html' ? 'Create Report' : 'Upload'}
                  </Button>
                </FormActions>
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
                        <Badge tone={format === 'pdf' ? 'danger' : 'info'}>{format.toUpperCase()}</Badge>
                      </div>
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-xs text-slate-500">
                          {formatRelativeTime(report.inserted_at)}
                        </span>
                        {(report.tags?.length ?? 0) > 0 && (
                          <span className="text-xs text-slate-500 truncate">
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
                      className={buttonClass('secondary', 'sm')}
                    >
                      Open PDF
                    </a>
                  )}
                  {reportFormat(selectedReport) === 'html' && (
                    <Button size="sm"
                      onClick={() => startEditing(selectedReport)}
                    >
                      Edit
                    </Button>
                  )}
                  <Button variant="danger" size="sm"
                    onClick={() => setDeletingReport(selectedReport)}
                    disabled={deleting}
                  >
                    Delete
                  </Button>
                </div>
              </div>
              {editing && (
                <div className="px-6 py-3 border-b border-slate-200 bg-slate-50">
                  <form onSubmit={handleEditSubmit} className="space-y-3">
                    <Field label="Title">
                      <Input
                        type="text"
                        value={editTitle}
                        onChange={e => setEditTitle(e.target.value)}
                      />
                    </Field>
                    <Field label="Tags">
                      <TagsInput tags={editTags} onChange={setEditTags} />
                    </Field>
                    <Checkbox checked={editAllowScripts} onChange={e => setEditAllowScripts(e.target.checked)} label="Allow scripts" />
                    {editAllowScripts && (
                      <Field>
                        <Label>
                          Allowed External URLs
                          <span className="text-xs text-slate-500 ml-2">(for CSP connect-src)</span>
                        </Label>
                        <TagsInput
                          tags={editAllowedConnectUrls}
                          onChange={setEditAllowedConnectUrls}
                          placeholder="https://example.com/api/"
                        />
                        <p className="text-xs text-slate-500 mt-1">
                          External URLs this report can connect to
                        </p>
                      </Field>
                    )}
                    {editError && <Notice tone="error">{editError}</Notice>}
                    <div className="flex items-center gap-2">
                      <Button variant="primary" type="submit" disabled={editSubmitting}>
                        {editSubmitting ? 'Saving...' : 'Save Changes'}
                      </Button>
                      <Button type="button" onClick={() => setEditing(false)}>
                        Cancel
                      </Button>
                    </div>
                  </form>
                </div>
              )}
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
                          className={buttonClass('primary')}
                        >
                          Open in new tab
                        </a>
                      </div>
                    </div>
                  ) : (
                    <iframe
                      src={reportUrl(selectedReport)!}
                      sandbox={selectedReport.metadata?.allow_scripts ? "allow-same-origin allow-scripts" : "allow-same-origin"}
                      className="w-full h-full border-none"
                      title={reportTitle(selectedReport)}
                    />
                  )
                ) : (
                  <div className="flex items-center justify-center h-full text-slate-500">
                    Report file not available
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center text-slate-600">
              Select a report to view
            </div>
          )}
        </div>
      </div>

      {deletingReport && (
        <ConfirmDialog
          message={`Are you sure you want to delete "${reportTitle(deletingReport)}"?`}
          onConfirm={handleDelete}
          onCancel={() => setDeletingReport(null)}
        />
      )}
    </Page>
  )
}

export default ReportsPage

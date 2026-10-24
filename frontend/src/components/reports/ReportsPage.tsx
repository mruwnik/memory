import { useState, useEffect, useCallback, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useReports, type Report } from '@/hooks/useReports'
import { TagsInput } from '../sources/shared'
import { Badge, Button, Checkbox, ConfirmButton, EmptyState, ErrorState, Field, FormActions, Input, List, LoadingState, Modal, Notice, Page, PaneHeader, Row, SplitView, Textarea, ToggleGroup, buttonClass } from '@/components/shared/ui'
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

  const handleDelete = async (report: Report) => {
    setDeleting(true)
    try {
      await deleteReport(report.id)
      if (String(report.id) === selectedId) {
        setSearchParams({})
      }
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
        reportName(selectedReport) ?? undefined,
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
    const name = reportName(report)
    return name && `/reports/${name}`
  }

  const closeForm = () => { setShowForm(false); resetForm() }

  return (
    <Page
      title="Reports"
      width="full"
      fill
      actions={<Button variant="primary" onClick={() => { resetForm(); setShowForm(true) }}>New report</Button>}
    >
      <SplitView
        listTitle={<>
          {loading ? 'Loading...' : `${reports.length} report${reports.length !== 1 ? 's' : ''}`}
          {queued && <span className="block text-xs font-normal text-amber-800 mt-1">Report queued for processing...</span>}
        </>}
        list={loading ? <LoadingState />
          : error ? <ErrorState message={error} onRetry={loadReports} />
          : reports.length === 0 ? <div className="p-4"><EmptyState message="No reports yet." actionLabel="New report" onAction={() => setShowForm(true)} /></div>
          : (
            <List flush>
              {reports.map(report => {
                const format = reportFormat(report)
                return (
                  <Row
                    key={report.id}
                    title={reportTitle(report)}
                    onSelect={() => handleSelectReport(report)}
                    selected={String(report.id) === selectedId}
                    badges={<Badge tone={format === 'pdf' ? 'danger' : 'info'}>{format.toUpperCase()}</Badge>}
                    meta={[formatRelativeTime(report.inserted_at), (report.tags?.length ?? 0) > 0 && report.tags.slice(0, 3).join(', ')]}
                  />
                )
              })}
            </List>
          )}
        detail={selectedReport && (
          <>
            <PaneHeader
              title={reportTitle(selectedReport)}
              actions={<>
                {reportUrl(selectedReport) && reportFormat(selectedReport) === 'pdf' && (
                  <a href={reportUrl(selectedReport)!} target="_blank" rel="noopener noreferrer" className={buttonClass('secondary', 'sm')}>Open PDF</a>
                )}
                {reportFormat(selectedReport) === 'html' && <Button size="sm" onClick={() => startEditing(selectedReport)}>Edit</Button>}
                <ConfirmButton confirm={`Delete "${reportTitle(selectedReport)}"?`} onConfirm={() => handleDelete(selectedReport)} disabled={deleting} />
              </>}
            />
            <div className="flex-1 overflow-hidden">
              {!reportUrl(selectedReport) ? (
                <div className="flex items-center justify-center h-full text-slate-600">Report file not available</div>
              ) : reportFormat(selectedReport) === 'pdf' ? (
                <div className="flex flex-col items-center justify-center gap-4 h-full text-slate-600">
                  <p className="text-lg">PDF Report</p>
                  <a href={reportUrl(selectedReport)!} target="_blank" rel="noopener noreferrer" className={buttonClass('primary')}>Open in new tab</a>
                </div>
              ) : (
                <iframe
                  src={reportUrl(selectedReport)!}
                  sandbox={selectedReport.metadata?.allow_scripts ? "allow-same-origin allow-scripts" : "allow-same-origin"}
                  className="w-full h-full border-none"
                  title={reportTitle(selectedReport)}
                />
              )}
            </div>
          </>
        )}
        placeholder="Select a report to view"
        onBack={() => setSearchParams({})}
        backLabel="Reports"
      />

      {showForm && (
        <Modal title="New report" onClose={closeForm}>
          <form onSubmit={handleSubmit} className="space-y-4">
            <ToggleGroup label="Report source" value={formMode} onChange={setFormMode}
              options={[{ value: 'html', label: 'Write HTML' }, { value: 'upload', label: 'Upload file' }]} />
            <Field label="Title">
              <Input type="text" value={title} onChange={e => setTitle(e.target.value)} placeholder="Report title" />
            </Field>
            {formMode === 'html' ? (
              <Field label="HTML Content">
                <Textarea value={htmlContent} onChange={e => setHtmlContent(e.target.value)} className="h-40 font-mono text-xs"
                  placeholder="<h1>My Report</h1>&#10;<p>Content here...</p>" />
              </Field>
            ) : (
              <Field label="File (.html, .htm, .pdf)">
                <input type="file" accept=".html,.htm,.pdf" onChange={e => setFile(e.target.files?.[0] || null)} className="text-sm text-slate-600" />
              </Field>
            )}
            <ReportAccessFields tags={tags} onTags={setTags} allowScripts={allowScripts} onAllowScripts={setAllowScripts}
              urls={allowedConnectUrls} onUrls={setAllowedConnectUrls} />
            {formError && <Notice tone="error">{formError}</Notice>}
            <FormActions>
              <Button type="button" onClick={closeForm}>Cancel</Button>
              <Button variant="primary" type="submit" disabled={submitting}>
                {submitting ? 'Saving...' : formMode === 'html' ? 'Create report' : 'Upload'}
              </Button>
            </FormActions>
          </form>
        </Modal>
      )}

      {editing && selectedReport && (
        <Modal title="Edit report" onClose={() => setEditing(false)}>
          <form onSubmit={handleEditSubmit} className="space-y-4">
            <Field label="Title">
              <Input type="text" value={editTitle} onChange={e => setEditTitle(e.target.value)} />
            </Field>
            <ReportAccessFields tags={editTags} onTags={setEditTags} allowScripts={editAllowScripts} onAllowScripts={setEditAllowScripts}
              urls={editAllowedConnectUrls} onUrls={setEditAllowedConnectUrls} />
            {editError && <Notice tone="error">{editError}</Notice>}
            <FormActions>
              <Button type="button" onClick={() => setEditing(false)}>Cancel</Button>
              <Button variant="primary" type="submit" disabled={editSubmitting}>{editSubmitting ? 'Saving...' : 'Save changes'}</Button>
            </FormActions>
          </form>
        </Modal>
      )}
    </Page>
  )
}

// `filename` is relative to the storage root; /reports/ and reports_upsert take the name inside the
// reports directory, which is flat.
const reportName = (report: Report) => report.filename?.split('/').pop() ?? null

interface ReportAccessFieldsProps {
  tags: string[]
  onTags: (tags: string[]) => void
  allowScripts: boolean
  onAllowScripts: (allow: boolean) => void
  urls: string[]
  onUrls: (urls: string[]) => void
}

const ReportAccessFields = ({ tags, onTags, allowScripts, onAllowScripts, urls, onUrls }: ReportAccessFieldsProps) => (
  <>
    <Field label="Tags">
      <TagsInput tags={tags} onChange={onTags} />
    </Field>
    <Checkbox checked={allowScripts} onChange={e => onAllowScripts(e.target.checked)} label="Allow scripts" />
    {allowScripts && (
      <Field label="Allowed external URLs (for CSP connect-src)"
        hint="External URLs this report can connect to (e.g., for calling external MCP servers)">
        <TagsInput tags={urls} onChange={onUrls} placeholder="https://example.com/api/" />
      </Field>
    )}
  </>
)

export default ReportsPage

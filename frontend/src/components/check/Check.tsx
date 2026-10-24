import { useState, useEffect, useCallback } from 'react'
import { useCheck, PAGE_LIMIT, CheckJob, CheckMode, CheckStatus, AskBody } from '@/hooks/useCheck'
import { formatRelativeTime } from '@/components/sources/shared'
import { Badge, Button, ConfirmButton, EmptyState, Field, FormActions, Hint, List, LoadingState, Modal, Notice, Page, RefreshButton, Row, RowError, Select, Textarea, ToggleGroup, Toolbar, useActionNotice, type Tone } from '@/components/shared/ui'

const STATUS_TONES: Record<CheckStatus, Tone> = {
  queued: 'warning',
  in_flight: 'info',
  ok: 'success',
  error: 'danger',
  expired: 'neutral',
}

const STATUS_LABELS: Record<CheckStatus, string> = {
  queued: 'Queued',
  in_flight: 'In progress',
  ok: 'Answered',
  error: 'Error',
  expired: 'Expired',
}

const MODES: CheckMode[] = ['research', 'verify', 'link', 'deep-dive', 'investigation-team']

// `listJobs` fetches a single PAGE_LIMIT-sized page (see useCheck); the UI has no
// pagination. With CHECK retention at 14 days, that cap is assumed to comfortably
// cover a user's live questions, so paging is a deliberate v2 follow-up. When we
// do hit the cap the header counts and filter tabs only reflect the most-recent
// page, so we flag them as approximate instead of silently undercounting.


// --- Answer rendering ---
//
// The remote worker that resolves a check job isn't bound to a strict schema,
// but in practice answers carry a known shape: a `verdict` (optionally with a
// `confidence` and a short `verdict_reason`), a prose `summary`/`answer`, and
// for link-mode a list of `sources`/`links`. We render those nicely and fall
// back to JSON for anything we don't recognise, so an answer is never hidden.

const VERDICT_TONES: { match: string; tone: Tone }[] = [
  { match: 'true', tone: 'success' },
  { match: 'false', tone: 'danger' },
  { match: 'mixed', tone: 'warning' },
  { match: 'partial', tone: 'warning' },
]

function verdictTone(verdict: string): Tone {
  const key = verdict.toLowerCase().trim()
  // `true` is checked before `false` so "likely true" doesn't fall through.
  return VERDICT_TONES.find(({ match }) => key.includes(match))?.tone ?? 'neutral'
}

function formatConfidence(c: unknown): string | null {
  // Accept either a 0–1 fraction or an already-percentage number.
  if (typeof c === 'number') return `${Math.round(c <= 1 ? c * 100 : c)}%`
  if (typeof c === 'string' && c.trim()) return c.trim()
  return null
}

interface AnswerSource {
  url: string | null
  label: string
}

function pickString(o: Record<string, unknown>, keys: string[]): string | undefined {
  for (const k of keys) {
    if (typeof o[k] === 'string' && o[k]) return o[k] as string
  }
  return undefined
}

function extractSources(raw: unknown): AnswerSource[] {
  if (!Array.isArray(raw)) return []
  return raw.map((item): AnswerSource => {
    if (typeof item === 'string') {
      const isUrl = /^https?:\/\//i.test(item.trim())
      return { url: isUrl ? item.trim() : null, label: item }
    }
    if (item && typeof item === 'object') {
      const o = item as Record<string, unknown>
      const url = pickString(o, ['url', 'href', 'link'])
      const label = pickString(o, ['title', 'name', 'label'])
      return { url: url ?? null, label: label ?? url ?? JSON.stringify(item) }
    }
    return { url: null, label: String(item) }
  })
}

const KNOWN_ANSWER_KEYS = new Set([
  'verdict', 'verdict_reason', 'confidence', 'summary', 'answer', 'sources', 'links',
])

interface AnswerViewProps {
  result: Record<string, unknown>
}

const AnswerView = ({ result }: AnswerViewProps) => {
  const verdict = typeof result.verdict === 'string' ? result.verdict : null
  const verdictReason = typeof result.verdict_reason === 'string' ? result.verdict_reason : null
  const confidence = formatConfidence(result.confidence)
  const summary =
    typeof result.summary === 'string' ? result.summary :
    typeof result.answer === 'string' ? result.answer : null
  const sources = extractSources(result.sources ?? result.links)

  const extra = Object.fromEntries(
    Object.entries(result).filter(([k]) => !KNOWN_ANSWER_KEYS.has(k)),
  )
  const hasExtra = Object.keys(extra).length > 0

  // Nothing recognised — show the raw object rather than an empty Answer block.
  if (!verdict && !summary && sources.length === 0 && !hasExtra) {
    return <PreJson value={result} />
  }

  return (
    <div className="space-y-2">
      {(verdict || confidence || verdictReason) && (
        <div className="flex items-center gap-2 flex-wrap">
          {verdict && (
            <Badge tone={verdictTone(verdict)} className="uppercase">{verdict}</Badge>
          )}
          {confidence && <span className="text-xs text-slate-500">{confidence} confidence</span>}
          {verdictReason && <span className="text-xs text-slate-500 italic">{verdictReason}</span>}
        </div>
      )}
      {summary && <p className="text-sm text-slate-700 whitespace-pre-wrap break-words">{summary}</p>}
      {sources.length > 0 && (
        <div>
          <p className="text-xs font-medium text-slate-500 mb-1">Sources</p>
          <ul className="space-y-0.5">
            {sources.map((s, i) => (
              <li key={i} className="text-xs break-words">
                {s.url ? (
                  <a href={s.url} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline break-all">
                    {s.label}
                  </a>
                ) : (
                  <span className="text-slate-600">{s.label}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {hasExtra && <PreJson value={extra} />}
    </div>
  )
}

const PreJson = ({ value }: { value: unknown }) => (
  <pre className="text-xs bg-slate-50 rounded p-2 overflow-x-auto text-slate-600 whitespace-pre-wrap break-words">
    {JSON.stringify(value, null, 2)}
  </pre>
)

// --- Ask Form ---

interface AskFormProps {
  onAsk: (body: AskBody) => Promise<void>
  onCancel: () => void
  saving: boolean
  error: string | null
}

const AskForm = ({ onAsk, onCancel, saving, error }: AskFormProps) => {
  const [text, setText] = useState('')
  const [mode, setMode] = useState<CheckMode>('research')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!text.trim()) return
    await onAsk({ text: text.trim(), mode })
  }

  return (
    <Modal title="New question" onClose={onCancel}>
      <form onSubmit={handleSubmit} className="space-y-4">
        <Field label="Question">
          <Textarea value={text} onChange={e => setText(e.target.value)} rows={3}
            placeholder="What would you like to verify, research, or link?" autoFocus />
        </Field>
        <Field label="Mode">
          <Select value={mode} onChange={e => setMode(e.target.value as CheckMode)}>
            {MODES.map(m => <option key={m} value={m}>{m.charAt(0).toUpperCase() + m.slice(1)}</option>)}
          </Select>
        </Field>
        {error && <Notice tone="error">{error}</Notice>}
        <FormActions>
          <Button type="button" onClick={onCancel} disabled={saving}>Cancel</Button>
          <Button variant="primary" type="submit" disabled={saving || !text.trim()}>{saving ? 'Submitting...' : 'Ask'}</Button>
        </FormActions>
      </form>
    </Modal>
  )
}

// --- Job Row ---

interface JobRowProps {
  job: CheckJob
  onDelete: (jobId: string) => Promise<unknown>
}

const JobRow = ({ job, onDelete }: JobRowProps) => {
  // Only surface the answer for a successful job: an errored job can carry a
  // partial result, and showing both an Answer and the red error line is confusing.
  const result =
    job.status === 'ok' && job.result && Object.keys(job.result).length > 0 ? job.result : null

  return (
    <Row
      title={<span className="whitespace-pre-wrap">{job.text}</span>}
      badges={<>
        <Badge>{job.mode}</Badge>
        <Badge tone={STATUS_TONES[job.status] ?? 'neutral'}>{STATUS_LABELS[job.status] || job.status}</Badge>
      </>}
      meta={[
        `Asked ${formatRelativeTime(job.submitted_at)}`,
        job.completed_at && `Answered ${formatRelativeTime(job.completed_at)}`,
      ]}
      actions={<ConfirmButton confirm="Delete this question?" onConfirm={() => onDelete(job.job_id)} />}
    >
      {result && <AnswerView result={result} />}
      {job.status === 'error' && job.error && <RowError>{job.error}</RowError>}
    </Row>
  )
}

// --- Main Page ---

type StatusFilter = 'all' | CheckStatus

const FILTERS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'queued', label: 'Queued' },
  { value: 'in_flight', label: 'In progress' },
  { value: 'ok', label: 'Answered' },
  { value: 'error', label: 'Error' },
  { value: 'expired', label: 'Expired' },
]

const Check = () => {
  const { listJobs, ask, deleteJob } = useCheck()
  const [jobs, setJobs] = useState<CheckJob[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')

  const [showAsk, setShowAsk] = useState(false)
  const [asking, setAsking] = useState(false)
  const [askError, setAskError] = useState<string | null>(null)
  const { run, notice } = useActionNotice()

  // Always fetch the full list; filtering happens client-side for display so
  // the header counts stay accurate and switching tabs doesn't refetch/flash.
  // A background load (the 5s poll) skips the loading spinner and swallows
  // transient errors so the list updates in place without flashing or banners.
  const loadJobs = useCallback(async (background = false) => {
    if (!background) setLoading(true)
    try {
      const data = await listJobs()
      setJobs(data)
      setError(null)
    } catch (e) {
      if (!background) setError(e instanceof Error ? e.message : 'Failed to load questions')
    } finally {
      if (!background) setLoading(false)
    }
  }, [listJobs])

  useEffect(() => { loadJobs() }, [loadJobs])

  // Poll every 5s so answers and status changes appear without a manual refresh.
  useEffect(() => {
    const id = setInterval(() => loadJobs(true), 5000)
    return () => clearInterval(id)
  }, [loadJobs])

  const handleAsk = async (body: AskBody) => {
    setAsking(true)
    setAskError(null)
    try {
      await ask(body)
      setShowAsk(false)
      await loadJobs()
    } catch (e) {
      setAskError(e instanceof Error ? e.message : 'Failed to submit question')
    } finally {
      setAsking(false)
    }
  }

  const handleDelete = (jobId: string) => run(async () => {
    await deleteJob(jobId)
    setJobs(prev => prev.filter(j => j.job_id !== jobId))
  })

  // Counts come from the full set; the displayed list is filtered in-memory.
  const answeredCount = jobs.filter(j => j.status === 'ok').length
  const pendingCount = jobs.filter(j => j.status === 'queued' || j.status === 'in_flight').length
  const visibleJobs = statusFilter === 'all' ? jobs : jobs.filter(j => j.status === statusFilter)

  // We only fetched the most-recent page; at the cap, counts are lower bounds
  // and older jobs aren't shown. Surface a "+" suffix and a notice so the
  // numbers don't read as exact totals.
  const atCap = jobs.length >= PAGE_LIMIT
  const countSuffix = atCap ? '+' : ''

  return (
    <Page
      title="Check questions"
      description={<span className="flex flex-wrap gap-x-3">
        {pendingCount > 0 && <span>{pendingCount}{countSuffix} pending</span>}
        <span>{answeredCount}{countSuffix} answered</span>
      </span>}
      actions={<Button variant="primary" onClick={() => setShowAsk(true)}>New question</Button>}
      width="wide"
    >
      {showAsk && <AskForm onAsk={handleAsk} onCancel={() => setShowAsk(false)} saving={asking} error={askError} />}

      <Toolbar actions={<RefreshButton onClick={() => loadJobs()} label="Refresh question list" />}>
        <ToggleGroup label="Status" options={FILTERS} value={statusFilter} onChange={setStatusFilter} />
      </Toolbar>

      {error && <Notice tone="error" onRetry={() => loadJobs()}>{error}</Notice>}
      {notice}

      {!loading && atCap && <Hint>Showing the {PAGE_LIMIT} most recent questions.</Hint>}

      {loading ? <LoadingState message="Loading questions..." />
        : visibleJobs.length === 0 ? <EmptyState message={statusFilter === 'all' ? 'No questions yet' : `No ${statusFilter} questions`} />
        : <List>{visibleJobs.map(job => <JobRow key={job.job_id} job={job} onDelete={handleDelete} />)}</List>}
    </Page>
  )
}

export default Check

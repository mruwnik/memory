import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { useSources, type ForumSettings, type ForumSummary } from '@/hooks/useSources'
import { Button, Card, Checkbox, DetailList, ErrorState, Field, FieldRow, FormActions, Input, LoadingState, NewTabLink, Notice, Section, Stat, StatGrid, useActionNotice } from '@/components/shared/ui'
import { formatUserDate } from '@/utils/timezones'
import { IntervalInput, formatRelativeTime, numberValue } from '../shared'
import { useSourcesContext } from '../context'

type NumberSetting = { [K in keyof ForumSettings]: ForumSettings[K] extends number ? K : never }[keyof ForumSettings]
type Draft = { [K in keyof ForumSettings]: ForumSettings[K] extends number ? number | '' : ForumSettings[K] }

const NUMBER_FIELDS: { key: NumberSetting; label: string; hint?: string; min: number }[] = [
  { key: 'days_back', label: 'Look back (days)', hint: 'How far back each sync lists posts', min: 1 },
  { key: 'min_karma', label: 'Min post karma', min: -100 },
  { key: 'max_items', label: 'Max posts per sync', min: 1 },
]

const COMMENT_FIELDS: { key: NumberSetting; label: string; hint?: string; min: number }[] = [
  { key: 'min_comment_karma', label: 'Min comment karma', min: -100 },
  { key: 'max_comment_fetches', label: 'Max posts fetched per sync', hint: 'Posts whose comments are fetched in one run', min: 0 },
  { key: 'comment_recheck_divisor', label: 'Recheck divisor', hint: 'Older posts are rechecked every age ÷ this, at least daily', min: 1 },
  { key: 'comment_recheck_max_days', label: 'Longest recheck gap (days)', min: 1 },
  { key: 'comment_max_age_days', label: 'Stop rechecking after (days)', hint: 'Posts older than this get no new comments', min: 0 },
]

const syncSummary = (result: Record<string, unknown> | null) => {
  if (!result) return undefined
  const n = (key: string) => Number(result[key] ?? 0)
  return `${n('new_posts')} new posts · ${n('updated_posts')} updated · comments fetched for ${n('comment_fetches')} posts`
}

const every = (minutes: number) => minutes % 60 === 0 ? `every ${minutes / 60}h` : `every ${minutes} min`

const settingsFacts = (s: ForumSettings): [string, string][] => [
  ['Schedule', s.active ? every(s.check_interval) : 'Off'],
  ['Posts', `last ${s.days_back} days, karma ≥ ${s.min_karma}, up to ${s.max_items} per sync${s.af ? ', Alignment Forum only' : ''}`],
  ['Comments', s.comments_enabled
    ? `karma ≥ ${s.min_comment_karma}, up to ${s.max_comment_fetches} posts per sync, rechecked every age ÷ ${s.comment_recheck_divisor} (at most ${s.comment_recheck_max_days} days) until ${s.comment_max_age_days} days old`
    : 'Off'],
]

const SettingsForm = ({ settings, onSaved }: { settings: ForumSettings; onSaved: (s: ForumSettings) => void }) => {
  const { updateForumSettings } = useSources()
  const [draft, setDraft] = useState<Draft>(settings)
  const [saving, setSaving] = useState(false)
  const { run, notice } = useActionNotice()
  const tooOften = Number(draft.check_interval) < 60
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft(d => ({ ...d, [key]: value }))

  const numberField = ({ key, label, hint, min }: { key: NumberSetting; label: string; hint?: string; min: number }) => (
    <Field key={key} label={label} hint={hint}>
      <Input type="number" min={min} value={draft[key]} onChange={e => set(key, numberValue(e.target.value))} required />
    </Field>
  )

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    await run(async () => onSaved(await updateForumSettings(draft as ForumSettings)), 'Forum sync settings saved.')
    setSaving(false)
  }

  return (
    <Card title="Scheduled sync" description="How often LessWrong is synced, and how much each sync fetches.">
      <form onSubmit={submit} aria-label="Scheduled sync settings" className="space-y-4">
        <Checkbox label="Sync on a schedule" checked={draft.active} onChange={e => set('active', e.target.checked)} />
        <IntervalInput label="Sync every" value={Number(draft.check_interval)} onChange={v => set('check_interval', v)} />
        {tooOften && <Notice tone="warning">Sync at most once an hour.</Notice>}
        <FieldRow className="md:grid-cols-3">{NUMBER_FIELDS.map(numberField)}</FieldRow>
        <Checkbox label="Alignment Forum only" checked={draft.af} onChange={e => set('af', e.target.checked)} />
        <Checkbox label="Fetch comments" checked={draft.comments_enabled} onChange={e => set('comments_enabled', e.target.checked)} />
        {draft.comments_enabled && <FieldRow className="md:grid-cols-3">{COMMENT_FIELDS.map(numberField)}</FieldRow>}
        {notice}
        <FormActions>
          <Button variant="primary" type="submit" disabled={saving || tooOften}>{saving ? 'Saving...' : 'Save settings'}</Button>
        </FormActions>
      </form>
    </Card>
  )
}

const ManualSync = () => {
  const { apiCall } = useAuth()
  const [syncing, setSyncing] = useState(false)
  const { run, notice } = useActionNotice()
  const [minKarma, setMinKarma] = useState<number | ''>(10)
  const [limit, setLimit] = useState<number | ''>(50)
  const [maxItems, setMaxItems] = useState<number | ''>(1000)
  const [daysBack, setDaysBack] = useState<number | ''>(30)
  const [af, setAf] = useState(false)

  const handleSync = async (e: React.FormEvent) => {
    e.preventDefault()
    setSyncing(true)
    await run(async () => {
      const sinceDate = new Date()
      sinceDate.setDate(sinceDate.getDate() - Number(daysBack))
      const response = await apiCall('/forums/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ since: sinceDate.toISOString(), min_karma: minKarma, limit, max_items: maxItems, af, tags: [] }),
      })
      if (!response.ok) {
        const data = await response.json()
        throw new Error(data.detail || 'Sync failed')
      }
    }, 'LessWrong sync started. Posts will be indexed in the background.')
    setSyncing(false)
  }

  return (
    <Card title="Sync now" description="Run a one-off sync with these settings. Comments follow the scheduled sync settings, and the next scheduled sync counts from this one.">
      <form onSubmit={handleSync} aria-label="Sync now" className="space-y-4">
        <FieldRow className="md:grid-cols-4">
          <Field label="Days back">
            <Input type="number" value={daysBack} onChange={e => setDaysBack(numberValue(e.target.value))} required min={1} max={365} />
          </Field>
          <Field label="Min karma">
            <Input type="number" value={minKarma} onChange={e => setMinKarma(numberValue(e.target.value))} required min={0} />
          </Field>
          <Field label="Posts per request">
            <Input type="number" value={limit} onChange={e => setLimit(numberValue(e.target.value))} required min={1} max={100} />
          </Field>
          <Field label="Max items">
            <Input type="number" value={maxItems} onChange={e => setMaxItems(numberValue(e.target.value))} required min={1} max={10000} />
          </Field>
        </FieldRow>
        <Checkbox label="Alignment Forum only" checked={af} onChange={e => setAf(e.target.checked)} />
        {notice}
        <FormActions>
          <Button variant="primary" type="submit" disabled={syncing}>{syncing ? 'Syncing...' : 'Sync LessWrong'}</Button>
        </FormActions>
      </form>
    </Card>
  )
}

export const ForumsPanel = () => {
  const { hasScope } = useAuth()
  const { getForumSummary } = useSources()
  const { refreshCounts } = useSourcesContext()
  const [summary, setSummary] = useState<ForumSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const isAdmin = hasScope('*')

  const load = useCallback(async () => {
    setError(null)
    try {
      setSummary(await getForumSummary())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load forum summary')
    }
  }, [getForumSummary])

  useEffect(() => { load() }, [load])

  if (error) return <ErrorState message={error} onRetry={load} />
  if (!summary) return <LoadingState />

  const newest = summary.newest_post
  const onSaved = (settings: ForumSettings) => {
    setSummary({ ...summary, settings })
    refreshCounts()
  }

  return (
    <Section title="Forums (LessWrong)" description="Posts and comments from LessWrong, indexed for search.">
      <StatGrid>
        <Stat label="Posts" value={summary.posts.toLocaleString()} />
        <Stat label="Comments" value={summary.comments.toLocaleString()} />
        <Stat label="Last sync" value={formatRelativeTime(summary.last_sync_at)}
          tone={summary.sync_error ? 'danger' : 'neutral'} hint={syncSummary(summary.last_sync_result)} />
      </StatGrid>
      {summary.sync_error && <Notice tone="error">Last sync failed: {summary.sync_error}</Notice>}
      <Card title="Overview">
        <DetailList items={[
          ['Newest post', newest && (newest.url
            ? <NewTabLink href={newest.url}>{newest.title || newest.url}</NewTabLink>
            : newest.title)],
          ['Published', newest?.published_at && formatUserDate(new Date(newest.published_at))],
          ...settingsFacts(summary.settings),
        ]} />
      </Card>
      {isAdmin && <SettingsForm settings={summary.settings} onSaved={onSaved} />}
      {isAdmin && <ManualSync />}
    </Section>
  )
}

export default ForumsPanel

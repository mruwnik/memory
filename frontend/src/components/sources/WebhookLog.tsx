import { useCallback, useEffect, useRef, useState, type SyntheticEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useWebhookLog, WEBHOOK_SOURCE_NAMES as SOURCE_NAMES, type WebhookAccount, type WebhookEvent, type WebhookOutcome } from '@/hooks/useWebhookLog'
import { useDebounce } from '@/hooks/useDebounce'
import { formatRelativeTime } from '@/components/sources/shared'
import { formatUserDateTime } from '@/utils/timezones'
import { deliveryLabel, deliveryTone } from '@/components/agents/subscriptions'
import { Badge, Button, Disclosure, EmptyState, Field, Hint, Input, List, LoadingState, Notice, Page, RefreshButton, Row, RowError, Select, SubList, Toolbar, type Tone } from '@/components/shared/ui'

const PAGE = 50
export const OUTCOMES: Record<WebhookOutcome, { label: string; tone: Tone }> = {
  stored: { label: 'stored', tone: 'success' },
  no_item_type: { label: 'no item type', tone: 'info' },
  not_collected: { label: 'not collected', tone: 'neutral' },
  rejected: { label: 'rejected', tone: 'danger' },
}

/** An older page after what is shown, without any delivery already shown. */
export const mergeOlder = (shown: WebhookEvent[], page: WebhookEvent[]) => {
  const ids = new Set(shown.map(e => e.id))
  return [...shown, ...page.filter(e => !ids.has(e.id))]
}

const accountKey = (a: Pick<WebhookAccount, 'source' | 'account_id'>) => `${a.source}:${a.account_id}`

// formatRelativeTime capitalises "Just now" for a line of its own; here it sits mid-sentence.
const ago = (at: string) => formatRelativeTime(at).replace(/^Just now$/, 'just now')

/** One delivery's facts; a not-collected stub has only its time and outcome. */
export const eventMeta = (e: WebhookEvent) => [
  `${formatUserDateTime(new Date(e.created_at))} (${ago(e.created_at)})`,
  e.container && `in ${e.container_name ?? e.container}`,
  e.item_ids.length > 0 && `stored as item${e.item_ids.length > 1 ? 's' : ''} ${e.item_ids.join(', ')}`,
  // A failing sync explains the missing items instead; the row shows it.
  e.outcome === 'stored' && e.item_ids.length === 0 && !e.container_error && (e.links_items
    ? 'no item linked: still queued, nothing new, or one you can\'t read'
    : 'the items it updates aren\'t linked to deliveries'),
]

/** What the payload view says in place of a payload, or null to show it. */
export const payloadNote = (e: WebhookEvent | undefined): string | null => {
  if (!e) return 'This delivery is no longer in the log.'
  if (e.payload_withheld) return 'Withheld: you can\'t read what is stored from where it came.'
  const p = e.payload as { truncated?: unknown; bytes?: unknown } | null
  if (p === null || p === undefined) return 'Not kept: it has expired.'
  const marker = typeof p === 'object' && !Array.isArray(p) && p.truncated === true && Object.keys(p).every(k => k === 'truncated' || k === 'bytes')
  if (!marker) return null
  return typeof p.bytes === 'number' ? `Not kept: too large (${Math.ceil(p.bytes / 1024)} KB).` : 'Not kept: nested too deeply.'
}

type PayloadState = { loading: true } | { event: WebhookEvent | undefined } | { error: string }

const PayloadView = ({ load }: { load: () => Promise<WebhookEvent | undefined> }) => {
  const [state, setState] = useState<PayloadState | null>(null)
  const onToggle = (ev: SyntheticEvent<HTMLDetailsElement>) => {
    if (!ev.currentTarget.open || (state && !('error' in state))) return
    setState({ loading: true })
    load().then(event => setState({ event }), (err: Error) => setState({ error: err.message }))
  }
  const note = state && 'event' in state ? payloadNote(state.event) : null
  return (
    <Disclosure summary="Payload" onToggle={onToggle}>
      {state && 'loading' in state && <Hint>Loading...</Hint>}
      {state && 'error' in state && <RowError>{state.error}</RowError>}
      {note && <Hint>{note}</Hint>}
      {state && 'event' in state && !note && (
        <pre className="max-h-96 overflow-auto whitespace-pre-wrap wrap-anywhere rounded bg-slate-50 p-2 text-xs">{JSON.stringify(state.event?.payload, null, 2)}</pre>
      )}
    </Disclosure>
  )
}

const EventRow = ({ e, loadPayload }: { e: WebhookEvent; loadPayload: (id: number) => Promise<WebhookEvent | undefined> }) => (
  <Row
    title={e.type || 'Delivery'}
    muted={e.outcome === 'not_collected'}
    badges={<Badge tone={OUTCOMES[e.outcome]?.tone ?? 'neutral'}>{OUTCOMES[e.outcome]?.label ?? e.outcome}</Badge>}
    meta={eventMeta(e)}
  >
    {e.container_error && (
      <RowError>Syncing {e.container_name ?? e.container} is failing, so this delivery may have stored nothing: {e.container_error}</RowError>
    )}
    {/* Only deliveries that store nothing keep their payload. */}
    {e.outcome === 'no_item_type' && <PayloadView load={() => loadPayload(e.id)} />}
    {e.triggered.length > 0 && (
      <SubList title="Triggered">
        {e.triggered.map((t, i) => (
          <Row
            // A subscription can take several versions of one item.
            key={`${t.subscription_id}-${t.item_id}-${i}`}
            compact
            title={t.subscription}
            badges={<Badge tone={deliveryTone(t.status)}>{deliveryLabel(t.status)}</Badge>}
            meta={[t.item_id !== null ? `item ${t.item_id}` : 'the delivery itself', t.run_id && <Link to={`/agents/runs/${t.run_id}`} className="text-primary hover:underline">run</Link>]}
          />
        ))}
      </SubList>
    )}
  </Row>
)

export default function WebhookLog() {
  const { listAccounts, listEvents, getEvent } = useWebhookLog()
  const [params, setParams] = useSearchParams()
  const [accounts, setAccounts] = useState<WebhookAccount[] | null>(null)
  const [events, setEvents] = useState<WebhookEvent[] | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [eventsError, setEventsError] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [outcome, setOutcome] = useState<WebhookOutcome | ''>('')
  const [typeInput, setTypeInput] = useState('')
  const type = useDebounce(typeInput.trim(), 300)

  const loadAccounts = useCallback(() => {
    setLoadError(null)
    return listAccounts().then(setAccounts).catch(e => setLoadError(e.message))
  }, [listAccounts])
  useEffect(() => { loadAccounts() }, [loadAccounts])

  // The account in the URL, else the one with the latest delivery.
  const requested = params.get('account')
  const latest = accounts?.filter(a => a.last_event_id !== null).sort((a, b) => (b.last_event_id ?? 0) - (a.last_event_id ?? 0))[0]
  const found = accounts?.find(a => accountKey(a) === requested)
  const selected = found ?? latest ?? accounts?.[0]

  const source = selected?.source
  const accountId = selected?.account_id
  // The load each response belongs to: a response for an account or filter no
  // longer shown, or from before a refresh, is dropped.
  const generation = useRef(0)
  const loadEvents = useCallback(() => {
    if (source === undefined || accountId === undefined) return Promise.resolve()
    const mine = ++generation.current
    setEvents(null)
    setEventsError(null)
    setLoadingMore(false)
    return listEvents(source, accountId, { limit: PAGE, outcome: outcome || undefined, type })
      .then(page => { if (mine === generation.current) { setEvents(page); setHasMore(page.length === PAGE) } })
      .catch(e => { if (mine === generation.current) setEventsError(e.message) })
  }, [listEvents, source, accountId, outcome, type])
  useEffect(() => { loadEvents() }, [loadEvents])

  const loadOlder = async () => {
    if (source === undefined || accountId === undefined || !events?.length) return
    const mine = generation.current
    setLoadingMore(true)
    try {
      const page = await listEvents(source, accountId, { before: events[events.length - 1].id, limit: PAGE, outcome: outcome || undefined, type })
      if (mine !== generation.current) return
      setEvents(shown => mergeOlder(shown ?? [], page))
      setHasMore(page.length === PAGE)
    } catch (e) {
      if (mine === generation.current) setEventsError((e as Error).message)
    } finally {
      if (mine === generation.current) setLoadingMore(false)
    }
  }

  const loadPayload = (id: number) => source === undefined || accountId === undefined
    ? Promise.resolve(undefined) : getEvent(source, accountId, id)
  const filtered = outcome !== '' || type !== ''

  return (
    <Page
      title="Webhook log"
      description="Every webhook delivery to your sources, what was stored from it and what it triggered. Deliveries from untracked channels show only that they arrived."
      loading={accounts === null && !loadError && 'Loading sources...'}
      error={loadError}
      onRetry={loadAccounts}
    >
      {accounts?.length === 0 && <EmptyState message="None of your sources takes webhooks yet." />}
      {selected && (
        <Toolbar actions={<RefreshButton onClick={loadEvents} />}>
          <Field label="Source" className="sm:w-80">
            <Select value={accountKey(selected)} onChange={e => setParams({ account: e.target.value })}>
              {accounts?.map(a => (
                <option key={accountKey(a)} value={accountKey(a)}>
                  {SOURCE_NAMES[a.source]} · {a.name}{a.last_event_at ? ` · last ${ago(a.last_event_at)}` : ' · no deliveries'}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Outcome" className="sm:w-44">
            <Select value={outcome} onChange={e => setOutcome(e.target.value as WebhookOutcome | '')}>
              <option value="">Any</option>
              {Object.entries(OUTCOMES).map(([value, o]) => <option key={value} value={value}>{o.label}</option>)}
            </Select>
          </Field>
          <Field label="Type" className="sm:w-56">
            <Input type="search" placeholder="e.g. pull_request" value={typeInput} onChange={e => setTypeInput(e.target.value)} />
          </Field>
        </Toolbar>
      )}
      {requested && accounts && !found && selected && (
        <Notice tone="warning">The source in this link isn't one you can see, so {SOURCE_NAMES[selected.source]} · {selected.name} is shown.</Notice>
      )}
      {eventsError && <Notice tone="error" onRetry={loadEvents}>{eventsError}</Notice>}
      {selected && events === null && !eventsError && <LoadingState message="Loading deliveries..." />}
      {events?.length === 0 && <EmptyState message={filtered ? 'No deliveries match these filters.' : 'No webhook deliveries to this account yet.'} />}
      {!!events?.length && (
        <>
          <List>{events.map(e => <EventRow key={e.id} e={e} loadPayload={loadPayload} />)}</List>
          {hasMore && (
            <div className="flex justify-center">
              <Button onClick={loadOlder} disabled={loadingMore}>{loadingMore ? 'Loading...' : 'Older deliveries'}</Button>
            </div>
          )}
        </>
      )}
    </Page>
  )
}

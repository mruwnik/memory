import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useWebhookLog, WEBHOOK_SOURCE_NAMES as SOURCE_NAMES, type WebhookAccount, type WebhookEvent, type WebhookOutcome } from '@/hooks/useWebhookLog'
import { formatRelativeTime } from '@/components/sources/shared'
import { formatUserDateTime } from '@/utils/timezones'
import { deliveryLabel, deliveryTone } from '@/components/agents/subscriptions'
import { Badge, Button, EmptyState, Field, List, LoadingState, Notice, Page, RefreshButton, Row, Select, SubList, Toolbar, type Tone } from '@/components/shared/ui'

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

/** One delivery's facts; a not-collected stub has only its time and outcome. */
export const eventMeta = (e: WebhookEvent) => [
  `${formatUserDateTime(new Date(e.created_at))} (${formatRelativeTime(e.created_at)})`,
  e.container && `in ${e.container}`,
  e.item_ids.length > 0 && `stored as item${e.item_ids.length > 1 ? 's' : ''} ${e.item_ids.join(', ')}`,
]

const EventRow = ({ e }: { e: WebhookEvent }) => (
  <Row
    title={e.type ?? 'Delivery'}
    muted={e.outcome === 'not_collected'}
    badges={<Badge tone={OUTCOMES[e.outcome]?.tone ?? 'neutral'}>{OUTCOMES[e.outcome]?.label ?? e.outcome}</Badge>}
    meta={eventMeta(e)}
  >
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
  const { listAccounts, listEvents } = useWebhookLog()
  const [params, setParams] = useSearchParams()
  const [accounts, setAccounts] = useState<WebhookAccount[] | null>(null)
  const [events, setEvents] = useState<WebhookEvent[] | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [eventsError, setEventsError] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)

  const loadAccounts = useCallback(() => {
    setLoadError(null)
    return listAccounts().then(setAccounts).catch(e => setLoadError(e.message))
  }, [listAccounts])
  useEffect(() => { loadAccounts() }, [loadAccounts])

  // The account in the URL, else the one with the latest delivery.
  const latest = accounts?.filter(a => a.last_event_id !== null).sort((a, b) => (b.last_event_id ?? 0) - (a.last_event_id ?? 0))[0]
  const selected = accounts?.find(a => accountKey(a) === params.get('account')) ?? latest ?? accounts?.[0]

  const source = selected?.source
  const accountId = selected?.account_id
  // The load each response belongs to: a response for an account no longer shown,
  // or from before a refresh, is dropped.
  const generation = useRef(0)
  const loadEvents = useCallback(() => {
    if (source === undefined || accountId === undefined) return Promise.resolve()
    const mine = ++generation.current
    setEvents(null)
    setEventsError(null)
    setLoadingMore(false)
    return listEvents(source, accountId, undefined, PAGE)
      .then(page => { if (mine === generation.current) { setEvents(page); setHasMore(page.length === PAGE) } })
      .catch(e => { if (mine === generation.current) setEventsError(e.message) })
  }, [listEvents, source, accountId])
  useEffect(() => { loadEvents() }, [loadEvents])

  const loadOlder = async () => {
    if (source === undefined || accountId === undefined || !events?.length) return
    const mine = generation.current
    setLoadingMore(true)
    try {
      const page = await listEvents(source, accountId, events[events.length - 1].id, PAGE)
      if (mine !== generation.current) return
      setEvents(shown => mergeOlder(shown ?? [], page))
      setHasMore(page.length === PAGE)
    } catch (e) {
      if (mine === generation.current) setEventsError((e as Error).message)
    } finally {
      if (mine === generation.current) setLoadingMore(false)
    }
  }

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
                  {SOURCE_NAMES[a.source]} · {a.name}{a.last_event_at ? ` · last ${formatRelativeTime(a.last_event_at)}` : ' · no deliveries'}
                </option>
              ))}
            </Select>
          </Field>
        </Toolbar>
      )}
      {eventsError && <Notice tone="error" onRetry={loadEvents}>{eventsError}</Notice>}
      {selected && events === null && !eventsError && <LoadingState message="Loading deliveries..." />}
      {events?.length === 0 && <EmptyState message="No webhook deliveries to this account yet." />}
      {!!events?.length && (
        <>
          <List>{events.map(e => <EventRow key={e.id} e={e} />)}</List>
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

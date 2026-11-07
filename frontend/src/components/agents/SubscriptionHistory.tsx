import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAgents, type ItemDelivery, type Subscription, type TickDelivery } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { formatUserDateTime } from '@/utils/timezones'
import { Badge, LoadingState, Notice, Row, RowError, SubList } from '@/components/shared/ui'
import { StatusBadge } from './StatusBadge'
import { deliveryLabel, deliveryTone } from './subscriptions'

const HISTORY = 20

const RunLink = ({ runId, status }: { runId: string; status: ItemDelivery['run_status'] }) => (
  <Link to={`/agents/runs/${runId}`} className="inline-flex items-center gap-1 text-primary hover:underline">
    run {status && <StatusBadge status={status} />}
  </Link>
)

const ItemRow = ({ d }: { d: ItemDelivery }) => (
  <Row
    compact
    title={`Item ${d.item_id}`}
    badges={<Badge tone={deliveryTone(d.status)}>{deliveryLabel(d.status)}</Badge>}
    meta={[
      formatRelativeTime(d.created_at),
      d.not_before && `held until ${formatUserDateTime(new Date(d.not_before))}`,
      d.changes.length > 0 && d.changes.join(', '),
      d.coalesced.length > 0 && `replaces ${d.coalesced.length} earlier`,
      d.run_id && <RunLink runId={d.run_id} status={d.run_status} />,
    ]}
  >
    {d.error && <RowError>{d.error}</RowError>}
  </Row>
)

const TickRow = ({ t }: { t: TickDelivery }) => (
  <Row
    compact
    title={t.time ? formatUserDateTime(new Date(t.time)) : 'Tick'}
    badges={<Badge tone={deliveryTone(t.result?.status ?? 'pending')}>{deliveryLabel(t.result?.status ?? 'pending')}</Badge>}
    meta={[t.missed > 0 && `${t.missed} missed`, t.result?.run_id && <RunLink runId={t.result.run_id} status={null} />]}
  >
    {t.result?.error && <RowError>{t.result.error}</RowError>}
  </Row>
)

/** A subscription's waiting deliveries apart from those that ended, or a schedule's ticks. */
export function SubscriptionHistory({ subscription }: { subscription: Subscription }) {
  const { fetchSubscription } = useAgents()
  const [waiting, setWaiting] = useState<ItemDelivery[] | null>(null)
  const [ended, setEnded] = useState<(ItemDelivery | TickDelivery)[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const filter = subscription.kind === 'ingest'

  const load = useCallback(() => {
    setError(null)
    const pending = filter
      ? fetchSubscription(subscription.id, { pending: true, limit: HISTORY }).then(r => setWaiting(r.deliveries as ItemDelivery[]))
      : Promise.resolve(setWaiting([]))
    const history = fetchSubscription(subscription.id, filter ? { pending: false, limit: HISTORY } : { limit: HISTORY })
      .then(r => setEnded(r.deliveries))
    return Promise.all([pending, history]).catch(e => setError(e.message))
  }, [fetchSubscription, subscription.id, filter])
  // The page reloads the subscription after each action and refresh; its history follows when that changed it.
  const activity = JSON.stringify(subscription.activity)
  useEffect(() => { load() }, [load, activity])

  if (error) return <Notice tone="error" onRetry={load}>{error}</Notice>
  if (waiting === null || ended === null) return <LoadingState message="Loading deliveries..." />
  const more = subscription.activity.pending - waiting.length
  return (
    <>
      {filter && (
        <SubList title={`Waiting (${subscription.activity.pending})`} empty={waiting.length === 0 && 'Nothing waiting.'}
          footer={more > 0 && <p className="text-xs text-slate-500">{`${more} more waiting behind these`}</p>}>
          {waiting.map(d => <ItemRow key={`${d.item_id}-${d.created_at}`} d={d} />)}
        </SubList>
      )}
      <SubList title={filter ? 'Recent deliveries' : 'Recent ticks'} empty={ended.length === 0 && 'Nothing delivered yet.'}>
        {ended.map(d => 'event_id' in d
          ? <TickRow key={d.event_id} t={d} />
          : <ItemRow key={`${d.item_id}-${d.created_at}`} d={d} />)}
      </SubList>
    </>
  )
}


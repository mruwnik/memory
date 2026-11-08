import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useAgents, type Catalog, type Subscription, type SubscriptionUpsert, type Timer } from '@/hooks/useAgents'
import { useWebhookLog } from '@/hooks/useWebhookLog'
import { formatUserDateTime } from '@/utils/timezones'
import {
  Badge, Button, ButtonLink, ConfirmButton, EmptyState, List, Modal, Notice, Page, RefreshButton, Row, RowError, Section,
  ToggleGroup, Toolbar, useActionNotice,
} from '@/components/shared/ui'
import { SubscriptionEditor, type AgentOption } from './SubscriptionEditor'
import { SubscriptionHistory } from './SubscriptionHistory'
import {
  ago, deliveryLabel, deliveryTone, describeFilter, describeHandler, describeRaw, describeTimer, handlerKind, HANDLER_LABELS, HANDLERS_FOR, origin,
  sourceKind, SOURCE_LABELS, subscriptionState, type HandlerKind, type SourceKind,
} from './subscriptions'

type SourceFilter = 'all' | SourceKind
type HandlerFilter = 'all' | HandlerKind

const SOURCE_FILTERS: { value: SourceFilter; label: string }[] = [
  { value: 'all', label: 'All sources' }, { value: 'filter', label: SOURCE_LABELS.filter }, { value: 'raw', label: SOURCE_LABELS.raw },
  { value: 'cron', label: SOURCE_LABELS.cron }, { value: 'at', label: SOURCE_LABELS.at },
]
// Only the handlers the source can go to.
const handlerFilters = (source: SourceFilter): { value: HandlerFilter; label: string }[] => [
  { value: 'all', label: 'All handlers' },
  ...(source === 'all' ? Object.keys(HANDLER_LABELS) as HandlerKind[] : HANDLERS_FOR[source]).map(k => ({ value: k, label: HANDLER_LABELS[k] })),
]

// Marks the top-level rows, apart from history rows nested in them, for moving focus after a removal.
const ROW_MARK = 'subscription-row'

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

const sourceSummary = (s: Subscription, accountNames: Record<string, string>) => {
  if (s.kind === 'ingest') return describeFilter(s.config)
  if (s.kind === 'raw') return describeRaw(s.config.raw, accountNames)
  return `${s.config.cron}${s.enabled && s.next_at ? ` · next ${formatUserDateTime(new Date(s.next_at))}` : ''}`
}

const backlogFact = (s: Subscription) =>
  s.activity.pending > 0 && `${s.activity.pending} waiting, oldest ${ago(s.activity.oldest_pending_at)}`

const lastFact = (s: Subscription) => {
  const last = s.activity.last
  return last ? `last ${ago(last.at)}` : 'nothing delivered yet'
}

export default function SubscriptionsPage() {
  const { listSubscriptions, upsertSubscription, deleteSubscription, cancelTimer, listDefinitions, listSystemAgents, catalog } = useAgents()
  const { listAccounts } = useWebhookLog()
  // Webhook sources' account names, by "source:account id".
  const [accountNames, setAccountNames] = useState<Record<string, string>>({})
  useEffect(() => {
    listAccounts()
      .then(accounts => setAccountNames(Object.fromEntries(accounts.map(a => [`${a.source}:${a.account_id}`, a.name]))))
      .catch(() => setAccountNames({}))
  }, [listAccounts])
  const [params, setParams] = useSearchParams()
  const agentParam = params.get('agent')
  const [subscriptions, setSubscriptions] = useState<Subscription[] | null>(null)
  const [timers, setTimers] = useState<Timer[]>([])
  const [agents, setAgents] = useState<AgentOption[]>([])
  const [sourceTypes, setSourceTypes] = useState<Catalog['subscription_source_types']>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [source, setSourceState] = useState<SourceFilter>('all')
  const [handler, setHandler] = useState<HandlerFilter>('all')
  const setSource = (value: SourceFilter) => {
    setSourceState(value)
    if (!handlerFilters(value).some(h => h.value === handler)) setHandler('all')
  }
  const clearFilters = () => {
    setSourceState('all')
    setHandler('all')
    if (agentParam !== null) setParams({})
  }
  const listRef = useRef<HTMLDivElement>(null)
  const [editing, setEditingState] = useState<Subscription | 'new' | null>(params.get('new') ? 'new' : null)
  // ?new=1 opens the form once; a reload after it closed shouldn't reopen it.
  const setEditing = (value: Subscription | 'new' | null) => {
    setEditingState(value)
    if (value === null && params.has('new')) setParams(p => { p.delete('new'); return p }, { replace: true })
  }
  const [expanded, setExpanded] = useState<number | null>(null)
  const [warnings, setWarnings] = useState<string[]>([])
  // Subscriptions and timers have separate id spaces: `subscription:3`, `timer:3`.
  const [busy, setBusy] = useState<string | null>(null)
  const { run, notice } = useActionNotice()

  const load = useCallback(() => {
    setLoadError(null)
    return listSubscriptions()
      .then(r => { setSubscriptions(r.subscriptions); setTimers(r.timers) })
      .catch(e => setLoadError(e.message))
  }, [listSubscriptions])
  useEffect(() => { load() }, [load])

  // The editor's agent picker; a failure leaves it empty, and the server names a bad pick.
  useEffect(() => {
    Promise.all([listDefinitions(), catalog(), listSystemAgents().catch(() => [])])
      .then(([definitions, cat, system]) => {
        setSourceTypes(cat.subscription_source_types)
        setAgents([
          ...definitions.map(d => ({ value: String(d.id), label: d.name, group: 'Your agents' })),
          ...(cat.agents ?? []).map(a => ({ value: a.slug, label: a.title, group: 'Prebuilt' })),
          ...system.map(a => ({ value: a.slug, label: a.title, group: 'System' })),
        ])
      })
      .catch(() => setAgents([]))
  }, [listDefinitions, catalog, listSystemAgents])

  const agentNames = useMemo(() => Object.fromEntries(agents.map(a => [a.value, a.label])), [agents])
  const shown = (subscriptions ?? []).filter(s =>
    (source === 'all' || sourceKind(s) === source)
    && (handler === 'all' || handlerKind(s.handler) === handler)
    && (agentParam === null || ('agent' in s.handler && String(s.handler.agent) === agentParam)))
  const shownTimers = timers.filter(t =>
    (source === 'all' || source === 'at') && (handler === 'all' || handlerKind(t.handler) === handler)
    && (agentParam === null || ('agent' in t.handler && String(t.handler.agent) === agentParam)))

  // The clicked button stays enabled (marked busy), so it keeps keyboard focus.
  const act = async (key: string, action: () => Promise<unknown>, success: string) => {
    if (busy !== null) return
    setBusy(key)
    await run(async () => { await action(); await load() }, success)
    setBusy(null)
  }
  // A removed row's focus moves to the row now in its place, else to the list.
  // Applied once the reloaded list has rendered without the removed row.
  const focusRow = useRef<number | null>(null)
  const remove = (key: string, action: () => Promise<unknown>, success: string, index: number) =>
    act(key, async () => { await action(); focusRow.current = index }, success)
  useLayoutEffect(() => {
    const index = focusRow.current
    if (index === null) return
    focusRow.current = null
    const rows = listRef.current?.querySelectorAll<HTMLElement>(`li.${ROW_MARK}`)
    const next = rows?.[Math.min(index, rows.length - 1)]?.querySelector<HTMLElement>('button, a')
    ;(next ?? listRef.current)?.focus()
  }, [subscriptions, timers])

  const save = async (args: SubscriptionUpsert) => {
    const saved = await upsertSubscription(args)
    setWarnings(saved.warnings ?? [])
    setEditing(null)
    await load()
  }

  const toggle = (s: Subscription, resume = false) =>
    act(`subscription:${s.id}`, () => upsertSubscription({ subscription_id: s.id, enabled: !s.enabled, ...(resume ? { resume: true } : {}) }),
      `${s.enabled ? 'Paused' : 'Resumed'} ${s.name}`)

  const pendingTotal = (subscriptions ?? []).reduce((n, s) => n + s.activity.pending, 0)
  const tripped = (subscriptions ?? []).filter(s => s.state.tripped).length

  return (
    <Page
      title="Subscriptions"
      // A placeholder line keeps the header from growing once the counts arrive.
      description={!subscriptions ? '\u00a0' : [
        plural(subscriptions.length, 'subscription'),
        `${pendingTotal} waiting`,
        tripped > 0 && `${tripped} tripped`,
      ].filter(Boolean).join(' · ')}
      actions={<>
        <Button variant="primary" onClick={() => { setWarnings([]); setEditing('new') }}>New subscription</Button>
        <ButtonLink to="/sources/webhook-log">Webhook log</ButtonLink>
      </>}
      loading={subscriptions === null && !loadError && 'Loading subscriptions...'}
      error={loadError}
      onRetry={load}
    >
      <Toolbar actions={<RefreshButton onClick={load} />}>
        <ToggleGroup label="Source" options={SOURCE_FILTERS} value={source} onChange={setSource} />
        <ToggleGroup label="Handler" options={handlerFilters(source)} value={handler} onChange={setHandler} />
      </Toolbar>
      {agentParam !== null && (
        <Notice tone="info" onDismiss={() => setParams({})}>
          Showing what hands work to {agentNames[agentParam] ?? `agent ${agentParam}`}.
        </Notice>
      )}
      {notice}
      {warnings.map(w => <Notice key={w} tone="warning">{w}</Notice>)}
      {editing !== null && (
        <Modal title={editing === 'new' ? 'New subscription' : `Edit ${editing.name}`} onClose={() => setEditing(null)} size="xl">
          <SubscriptionEditor
            existing={editing === 'new' ? undefined : editing}
            presetAgent={agentParam ?? undefined}
            agents={agents}
            sourceTypes={sourceTypes}
            takenNames={(subscriptions ?? []).filter(s => editing === 'new' || s.id !== editing.id).map(s => s.name)}
            onSave={save}
            onCancel={() => setEditing(null)}
          />
        </Modal>
      )}
      {subscriptions?.length === 0 && timers.length === 0 && (
        <EmptyState message="Nothing subscribed yet. Your workers subscribe themselves, or start one here." />
      )}
      <div ref={listRef} tabIndex={-1} className="space-y-4 focus:outline-none">
      {shown.length > 0 && (
        <List separate>
          {shown.map((s, index) => {
            const state = subscriptionState(s)
            const last = s.activity.last
            return (
              <Row
                key={s.id}
                className={ROW_MARK}
                title={s.name}
                onSelect={() => setExpanded(expanded === s.id ? null : s.id)}
                expanded={expanded === s.id}
                muted={!s.enabled}
                badges={<>
                  <Badge>{SOURCE_LABELS[sourceKind(s)]}</Badge>
                  <Badge tone="primary">{describeHandler(s.handler, agentNames)}</Badge>
                  {state && <Badge tone={state.tone}>{state.label}</Badge>}
                  {last?.status && <Badge tone={deliveryTone(last.status)}>{deliveryLabel(last.status)}</Badge>}
                </>}
                meta={[origin(s), backlogFact(s), lastFact(s)]}
                actions={<>
                  {s.state.tripped && s.kind === 'ingest' && (
                    <Button size="sm" aria-busy={busy === `subscription:${s.id}`} onClick={() => toggle(s, true)}>Resume from trip</Button>
                  )}
                  <Button size="sm" aria-busy={busy === `subscription:${s.id}`} onClick={() => toggle(s)}>
                    {busy === `subscription:${s.id}` ? 'Saving...' : s.enabled ? 'Pause' : s.state.tripped ? 'Resume from now' : 'Resume'}
                  </Button>
                  <Button size="sm" onClick={() => { setWarnings([]); setEditing(s) }}>Edit</Button>
                  <ConfirmButton confirm={`Delete ${s.name} and its waiting deliveries?`} onConfirm={() => remove(`subscription:${s.id}`, () => deleteSubscription(s.id), `Deleted ${s.name}`, index)} />
                </>}
              >
                <p className="text-slate-600 break-words">{sourceSummary(s, accountNames)}</p>
                {s.state.tripped && <RowError>Tripped {ago(s.state.tripped.at)}: {s.state.tripped.reason}</RowError>}
                {s.state.tripped && s.kind === 'ingest' && (
                  <p className="text-slate-600">
                    Resume from trip also delivers what arrived while it was tripped, as far back as events are kept; resume from now
                    skips it.
                  </p>
                )}
                {s.state.throttled && (
                  <p className="text-amber-800">Throttled since {ago(s.state.throttled.since)}: {s.state.throttled.reason}</p>
                )}
                {expanded === s.id && <SubscriptionHistory subscription={s} />}
              </Row>
            )
          })}
        </List>
      )}
      {subscriptions !== null && subscriptions.length + timers.length > 0 && shown.length + shownTimers.length === 0 && (
        <EmptyState message="Nothing matches these filters." actionLabel="Clear filters" onAction={clearFilters} />
      )}
      {shownTimers.length > 0 && (
        <Section title="Timers" description="One-off deliveries waiting to fire.">
          <List>
            {shownTimers.map((t, index) => (
              <Row
                key={t.id}
                className={ROW_MARK}
                title={t.fire_at ? formatUserDateTime(new Date(t.fire_at)) : `Timer ${t.id}`}
                badges={<Badge tone="primary">{HANDLER_LABELS[handlerKind(t.handler)]}</Badge>}
                meta={[describeTimer(t, agentNames)]}
                actions={<ConfirmButton confirm="Cancel this timer?" confirmLabel="Cancel timer" cancelLabel="Keep" onConfirm={() => remove(`timer:${t.id}`, () => cancelTimer(t.id), 'Cancelled the timer', shown.length + index)}>Cancel</ConfirmButton>}
              />
            ))}
          </List>
        </Section>
      )}
      </div>
    </Page>
  )
}

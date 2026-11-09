import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useDebounce } from '@/hooks/useDebounce'
import { useIngestedItems, type EmbedStatus, type IngestedItem, type ItemConnection } from '@/hooks/useIngestedItems'
import { EmptyState, ErrorState, Input, List, LoadingState, Page, Pager, RefreshButton, Select, ToggleGroup, Toolbar } from '@/components/shared/ui'
import { ItemRow, typeLabel } from './ItemRow'

const PAGE_SIZE = 50

type StatusFilter = 'all' | 'indexed' | 'pending' | 'failed' | 'skipped'
const STATUS_FILTERS: { value: StatusFilter; label: string; statuses?: EmbedStatus[] }[] = [
  { value: 'all', label: 'All' },
  { value: 'indexed', label: 'Indexed', statuses: ['STORED'] },
  { value: 'pending', label: 'Awaiting indexing', statuses: ['RAW', 'QUEUED'] },
  { value: 'failed', label: 'Failed', statuses: ['FAILED'] },
  { value: 'skipped', label: 'Not indexed', statuses: ['SKIPPED'] },
]

const message = (e: unknown, fallback: string) => e instanceof Error ? e.message : fallback

/**
 * Every stored item the user can access, newest first, a page at a time: narrowed to one content type
 * and, for types that come from a connection (email account, channel, feed, repo...), one connection,
 * and searched by title, subject, sender or filename. The filters live in the URL.
 */
export default function IngestedItems() {
  const { listItems, countItems } = useIngestedItems()
  const [params, setParams] = useSearchParams()
  const itemType = params.get('type') ?? ''
  const connectionId = params.get('connection') ?? ''
  const query = params.get('q') ?? ''
  const status = (STATUS_FILTERS.find(f => f.value === params.get('status'))?.value ?? 'all')
  const page = Math.max(0, Number(params.get('page')) || 0)

  const [text, setText] = useState(query)
  const typed = useDebounce(text, 300)
  const [items, setItems] = useState<IngestedItem[] | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [types, setTypes] = useState<Record<string, number>>({})
  const [connections, setConnections] = useState<ItemConnection[] | null>(null)
  const latest = useRef(0)

  /** Change filters; any change but paging starts again from the first page. */
  const update = useCallback((changes: Record<string, string>) => {
    setParams(current => {
      const next = new URLSearchParams(current)
      Object.entries(changes).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key))
      if (!('page' in changes)) next.delete('page')
      return next
    }, { replace: !('page' in changes) })
  }, [setParams])

  // Only typing changes the search: going back in history must not be undone by the debounced text.
  const lastTyped = useRef(typed)
  useEffect(() => {
    if (typed === lastTyped.current) return
    lastTyped.current = typed
    update({ q: typed.trim() })
  }, [typed, update])

  const load = useCallback(async () => {
    const request = ++latest.current
    setBusy(true)
    try {
      const statuses = STATUS_FILTERS.find(f => f.value === status)?.statuses
      const result = await listItems(
        { itemTypes: itemType ? [itemType] : [], statuses, contains: query, connectionId },
        { limit: PAGE_SIZE, offset: page * PAGE_SIZE },
      )
      if (request !== latest.current) return
      setItems(result.items)
      setHasMore(result.hasMore)
      setError(null)
    } catch (e) {
      if (request === latest.current) setError(message(e, 'Could not load items'))
    } finally {
      if (request === latest.current) setBusy(false)
    }
  }, [listItems, itemType, status, query, connectionId, page])
  useEffect(() => { void load() }, [load])

  // Filter choices are a convenience: if counting fails the list still works, with fewer options.
  useEffect(() => {
    countItems().then(counts => setTypes(counts.byType), () => setTypes({}))
  }, [countItems])
  useEffect(() => {
    setConnections(null)
    if (!itemType) return
    let current = true
    countItems({ itemTypes: [itemType] }).then(
      counts => { if (current) setConnections(counts.byConnection) },
      () => { if (current) setConnections(null) },
    )
    return () => { current = false }
  }, [countItems, itemType])

  const typeOptions = Object.keys({ ...types, ...(itemType ? { [itemType]: 0 } : {}) }).sort()
  const filtered = !!(query || connectionId || status !== 'all')
  const connectionName = connections?.find(c => c.id === connectionId)?.name

  return (
    <Page
      title={itemType ? typeLabel(itemType) : 'All items'}
      back={{ to: '/ingestion', label: 'Ingestion overview' }}
      description={[
        connectionName && `From ${connectionName}.`,
        'Stored items you can access, newest first. Search matches titles, subjects, senders, authors and filenames, not the text itself.',
      ].filter(Boolean).join(' ')}
    >
      <Toolbar actions={<RefreshButton onClick={load} loading={busy} />}>
        <Input type="search" aria-label="Search items" placeholder="Search titles, subjects, filenames" className="sm:w-64"
          value={text} onChange={e => setText(e.target.value)} />
        <Select aria-label="Content type" className="sm:w-48" value={itemType}
          onChange={e => update({ type: e.target.value, connection: '' })}>
          <option value="">All content types</option>
          {typeOptions.map(name => <option key={name} value={name}>{typeLabel(name)}</option>)}
        </Select>
        {!!connections?.length && (
          <Select aria-label="Connection" className="sm:w-56" value={connectionId} onChange={e => update({ connection: e.target.value })}>
            <option value="">All connections</option>
            {connections.map(c => <option key={c.id} value={c.id}>{c.name ?? c.id} ({c.count.toLocaleString()})</option>)}
          </Select>
        )}
        <ToggleGroup label="Indexing status" options={STATUS_FILTERS} value={status}
          onChange={value => update({ status: value === 'all' ? '' : value })} />
      </Toolbar>
      {error ? <ErrorState message={error} onRetry={load} />
        : items === null ? <LoadingState message="Loading items..." />
        : items.length === 0 ? <EmptyState message={filtered || page > 0 ? 'No items match these filters' : 'No items you can see yet'} />
        : <List>{items.map(item => <ItemRow key={item.id} item={item} showType={!itemType} />)}</List>}
      {(page > 0 || hasMore) && !error && <Pager page={page} hasMore={hasMore} onPage={next => update({ page: next ? String(next) : '' })} />}
    </Page>
  )
}

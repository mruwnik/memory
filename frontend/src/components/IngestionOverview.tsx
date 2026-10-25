import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/hooks/useAuth'
import { apiError } from '@/hooks/apiError'
import { ButtonLink, Card, EmptyState, ErrorState, LoadingState, Page, RefreshButton, Section, Stat, StatGrid } from '@/components/shared/ui'
import { formatUserDateTime } from '@/utils/timezones'

interface Counts {
  total: number
  indexed: number
  pending: number
  failed: number
  skipped: number
  added_last_24h: number
}
interface Overview extends Counts {
  by_type: (Counts & { item_type: string })[]
  last_ingested_at: string | null
}
const typeLabel = (name: string) => name.replaceAll('_', ' ').replace(/^./, char => char.toUpperCase())

export default function IngestionOverview({ standalone = false }: { standalone?: boolean }) {
  const { apiCall } = useAuth()
  const [data, setData] = useState<Overview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(async () => {
    setBusy(true)
    try {
      const response = await apiCall('/ingestion-overview')
      if (!response.ok) throw await apiError(response, 'Could not load ingestion overview')
      setData(await response.json())
      setError(null)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not load ingestion overview') }
    finally { setBusy(false) }
  }, [apiCall])
  useEffect(() => { void load() }, [load])
  const Container = standalone ? Page : Section
  return <Container title="Ingestion overview" description="Stored items you can access, including imported content and items created in Memory. Messages and book sections count individually; search chunks do not."
    actions={<>{!standalone && <ButtonLink to="/ui/ingestion">View ingestion overview</ButtonLink>}<RefreshButton onClick={load} disabled={busy} /></>}>
    {error && <ErrorState message={error} onRetry={load} />}
    {!data && !error && <LoadingState message="Loading ingestion counts..." />}
    {data && <>
      <StatGrid>
        <Stat label="Total items" value={data.total.toLocaleString()} />
        <Stat label="Added in last 24 hours" value={data.added_last_24h.toLocaleString()} />
        <Stat label="Indexed" value={data.indexed.toLocaleString()} hint="Completed indexing" />
        <Stat label="Awaiting indexing" value={data.pending.toLocaleString()} hint="New or queued items" />
        <Stat label="Indexing failed" value={data.failed.toLocaleString()} />
        <Stat label="Indexing skipped" value={data.skipped.toLocaleString()} hint="Stored without indexing" />
      </StatGrid>
      {data.last_ingested_at && <p className="text-sm text-slate-600">Last item added: {formatUserDateTime(new Date(data.last_ingested_at))}</p>}
      {data.total === 0 ? <EmptyState message="No items ingested yet" action={<ButtonLink to="/ui/sources">Connect a source</ButtonLink>} /> :
        <Card title="Items by content type">
          <dl className="grid gap-x-8 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
            {data.by_type.map(group => <div key={group.item_type} className="flex justify-between gap-4">
              <dt>{typeLabel(group.item_type)}</dt><dd className="font-semibold tabular-nums">{group.total.toLocaleString()}</dd>
            </div>)}
          </dl>
        </Card>}
    </>}
  </Container>
}

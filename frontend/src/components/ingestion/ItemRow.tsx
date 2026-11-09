import { useCallback, useState } from 'react'
import { useIngestedItems, type EmbedStatus, type IngestedItem } from '@/hooks/useIngestedItems'
import { Badge, Button, ButtonLink, ExpandToggle, Row, RowError, SubList, buttonClass, type Tone } from '@/components/shared/ui'
import { fileUrl } from '@/utils/files'
import { formatBytes } from '@/utils/format'
import { formatUserDateTime } from '@/utils/timezones'

export const typeLabel = (name: string) => name.replaceAll('_', ' ').replace(/^./, char => char.toUpperCase())

export const itemsUrl = (itemType?: string) => itemType ? `/ingestion/items?type=${encodeURIComponent(itemType)}` : '/ingestion/items'

const STATUS: Record<EmbedStatus, [string, Tone]> = {
  STORED: ['indexed', 'success'],
  RAW: ['awaiting indexing', 'neutral'],
  QUEUED: ['queued for indexing', 'info'],
  FAILED: ['indexing failed', 'danger'],
  SKIPPED: ['not indexed', 'warning'],
}

const MAX_TITLE = 160

/** One line for the row title: chat messages carry their whole text as the title. */
export const itemTitle = (item: IngestedItem) => {
  const text = (item.title || item.filename || `Item ${item.id}`).replace(/\s+/g, ' ').trim()
  return text.length > MAX_TITLE ? `${text.slice(0, MAX_TITLE - 1)}…` : text
}

/** A stored item: title, type and indexing status, when it was stored, its size and id; the preview toggles open. */
export const ItemRow = ({ item, showType }: { item: IngestedItem; showType?: boolean }) => {
  const [open, setOpen] = useState(false)
  const [status, tone] = STATUS[item.embed_status] ?? [item.embed_status, 'neutral']
  const preview = item.preview?.trim()
  return (
    <Row
      title={itemTitle(item)}
      onSelect={preview ? () => setOpen(was => !was) : undefined}
      expanded={preview ? open : undefined}
      badges={<>{showType && item.type && <Badge>{typeLabel(item.type)}</Badge>}<Badge tone={tone}>{status}</Badge></>}
      meta={[
        item.inserted_at && formatUserDateTime(item.inserted_at),
        item.size != null && formatBytes(item.size),
        item.title && item.filename,
        `#${item.id}`,
      ]}
      actions={item.filename && <a href={fileUrl(item.filename)} className={buttonClass('secondary', 'sm')}>Open file</a>}
    >
      {open && <p className="text-sm text-slate-700 whitespace-pre-wrap break-words">{preview}</p>}
    </Row>
  )
}

/** The newest items of one type, loaded the first time the list is opened. */
export const LatestItems = ({ itemType, count = 5 }: { itemType: string; count?: number }) => {
  const { listItems } = useIngestedItems()
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<IngestedItem[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    setError(null)
    try {
      setItems((await listItems({ itemTypes: [itemType] }, { limit: count })).items)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the latest items')
    }
  }, [listItems, itemType, count])
  const toggle = () => {
    setOpen(!open)
    if (!open && items === null) void load()
  }
  const label = typeLabel(itemType).toLowerCase()
  return (
    <SubList
      title={<ExpandToggle expanded={open} onClick={toggle}>Latest {label} items</ExpandToggle>}
      actions={open && <ButtonLink to={itemsUrl(itemType)} size="sm" variant="link">View all</ButtonLink>}
      empty={open && !error && (items === null ? 'Loading latest items...' : 'No items you can see yet')}
      footer={open && error && <RowError>{error} <Button size="sm" variant="link" onClick={load}>Retry</Button></RowError>}
    >
      {open && items?.map(item => <ItemRow key={item.id} item={item} />)}
    </SubList>
  )
}

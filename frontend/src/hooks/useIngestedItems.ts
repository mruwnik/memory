import { useCallback } from 'react'
import { useMCP } from '@/hooks/useMCP'

export const EMBED_STATUSES = ['RAW', 'QUEUED', 'STORED', 'FAILED', 'SKIPPED'] as const
export type EmbedStatus = typeof EMBED_STATUSES[number]

export interface IngestedItem {
  id: number
  modality: string
  type: string | null
  embed_status: EmbedStatus
  title: string | null
  filename: string | null
  mime_type: string | null
  size: number | null
  inserted_at: string | null
  preview: string | null
}

export interface ItemQuery {
  itemTypes?: string[]
  /** Indexing statuses to include; every status when omitted. */
  statuses?: readonly EmbedStatus[]
  /** Case-insensitive text matched against titles, subjects, senders and filenames. */
  contains?: string
  /** A connection id from `countItems().byConnection`, with `itemTypes` set. */
  connectionId?: string
}

export interface ItemConnection { id: string; name: string | null; count: number }

export interface ItemCounts {
  total: number
  byType: Record<string, number>
  /** Items per connection (email account, channel, feed, repo...), or null when the types have none in common. */
  byConnection: ItemConnection[] | null
}

const browseArgs = ({ itemTypes = [], statuses = EMBED_STATUSES, contains, connectionId }: ItemQuery) => ({
  item_types: itemTypes,
  embed_statuses: statuses,
  ...(contains?.trim() ? { contains: contains.trim() } : {}),
  ...(connectionId ? { connection_id: connectionId } : {}),
})

/** Browse stored items newest first, through the core_list_items and core_count_items MCP tools. */
export const useIngestedItems = () => {
  const { mcpCall } = useMCP()

  const listItems = useCallback(async (query: ItemQuery = {}, { limit = 50, offset = 0 } = {}) => {
    const [result] = await mcpCall<{ items?: IngestedItem[]; has_more?: boolean }[]>('core_list_items', {
      ...browseArgs(query),
      limit,
      offset,
      include_metadata: false,
      include_total: false,
    })
    return { items: result?.items ?? [], hasMore: !!result?.has_more }
  }, [mcpCall])

  const countItems = useCallback(async (query: ItemQuery = {}): Promise<ItemCounts> => {
    const [result] = await mcpCall<{ total?: number; by_type?: Record<string, number>; by_connection?: ItemConnection[] | null }[]>(
      'core_count_items', browseArgs(query))
    return { total: result?.total ?? 0, byType: result?.by_type ?? {}, byConnection: result?.by_connection ?? null }
  }, [mcpCall])

  return { listItems, countItems }
}

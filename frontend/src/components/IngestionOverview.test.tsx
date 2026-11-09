import { beforeEach, expect, it, vi } from 'vitest'
import { renderWithRouter, screen } from '@/test/utils'
import IngestionOverview from './IngestionOverview'
const apiCall = vi.fn()
const listItems = vi.fn()
vi.mock('@/hooks/useAuth', () => ({ useAuth: () => ({ apiCall }), SERVER_URL: '' }))
vi.mock('@/hooks/useIngestedItems', () => ({ useIngestedItems: () => ({ listItems }) }))
const counts = { indexed: 0, pending: 0, failed: 0, skipped: 0, added_last_24h: 0 }
const overview = {
  total: 1200, indexed: 1000, pending: 150, failed: 20, skipped: 30, added_last_24h: 25, last_ingested_at: null,
  by_type: [
    { ...counts, item_type: 'mail_message', total: 1150, failed: 20, added_last_24h: 25 },
    { ...counts, item_type: 'session_segment', total: 50 },
  ],
}
const latest = { id: 9, type: 'mail_message', embed_status: 'STORED', title: 'Newest mail', filename: null, size: 10, inserted_at: '2026-11-01T12:00:00Z', preview: null }
beforeEach(() => {
  vi.clearAllMocks()
  apiCall.mockResolvedValue({ ok: true, json: async () => overview })
  listItems.mockResolvedValue({ items: [latest], hasMore: false })
})
it('shows item totals and content types', async () => {
  renderWithRouter(<IngestionOverview />)
  expect(await screen.findByText('Indexed')).toBeInTheDocument()
  expect(screen.getByText('1,000')).toBeInTheDocument()
  expect(screen.getByText('Mail message')).toBeInTheDocument()
  expect(screen.getByText('25')).toBeInTheDocument()
  expect(screen.getByText('1,150 items')).toBeInTheDocument()
  expect(screen.getByText('20 failed')).toBeInTheDocument()
})
it('links each listed content type to its items', async () => {
  renderWithRouter(<IngestionOverview />)
  expect(await screen.findByRole('link', { name: 'Mail message' })).toHaveAttribute('href', '/ingestion/items?type=mail_message')
  expect(screen.getByRole('link', { name: 'Browse all items' })).toHaveAttribute('href', '/ingestion/items')
  // Session transcripts are counted but not browsable here.
  expect(screen.getByText('Session segment')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Session segment' })).not.toBeInTheDocument()
})
it('loads the latest items of a type when opened, on the standalone page', async () => {
  const { user } = renderWithRouter(<IngestionOverview standalone />)
  const toggle = await screen.findByRole('button', { name: 'Latest mail message items' })
  expect(listItems).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: 'Latest session segment items' })).not.toBeInTheDocument()

  await user.click(toggle)

  expect(await screen.findByText('Newest mail')).toBeInTheDocument()
  expect(listItems).toHaveBeenCalledWith({ itemTypes: ['mail_message'] }, { limit: 5 })
  expect(screen.getByRole('link', { name: 'View all' })).toHaveAttribute('href', '/ingestion/items?type=mail_message')
})
it('keeps the dashboard card compact', async () => {
  renderWithRouter(<IngestionOverview />)
  await screen.findByText('Mail message')
  expect(screen.queryByRole('button', { name: /^Latest/ })).not.toBeInTheDocument()
})
it('shows a failure to load the latest items with a retry', async () => {
  listItems.mockRejectedValueOnce(new Error('Unavailable'))
  const { user } = renderWithRouter(<IngestionOverview standalone />)
  await user.click(await screen.findByRole('button', { name: 'Latest mail message items' }))
  expect(await screen.findByText('Unavailable')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByText('Newest mail')).toBeInTheDocument()
})
it('explains an empty inventory and links to Sources', async () => {
  apiCall.mockResolvedValue({ ok: true, json: async () => ({ ...overview, total: 0, by_type: [] }) })
  renderWithRouter(<IngestionOverview />)
  expect(await screen.findByText('No items ingested yet')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Connect a source' })).toHaveAttribute('href', '/sources')
})
it('shows a load failure instead of pretending there are zero items', async () => {
  apiCall.mockRejectedValue(new Error('Unavailable'))
  renderWithRouter(<IngestionOverview />)
  expect(await screen.findByText('Unavailable')).toBeInTheDocument()
  expect(screen.queryByText('No items ingested yet')).not.toBeInTheDocument()
})

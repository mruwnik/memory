import { beforeEach, expect, it, vi } from 'vitest'
import { renderWithRouter, screen } from '@/test/utils'
import IngestionOverview from './IngestionOverview'
const apiCall = vi.fn()
vi.mock('@/hooks/useAuth', () => ({ useAuth: () => ({ apiCall }) }))
const overview = { total: 1200, indexed: 1000, pending: 150, failed: 20, skipped: 30, added_last_24h: 25, last_ingested_at: null, by_type: [{ item_type: 'mail_message', total: 1200 }] }
beforeEach(() => { vi.clearAllMocks() })
it('shows item totals and content types', async () => {
  apiCall.mockResolvedValue({ ok: true, json: async () => overview })
  renderWithRouter(<IngestionOverview />)
  expect(await screen.findByText('Indexed')).toBeInTheDocument()
  expect(screen.getByText('1,000')).toBeInTheDocument()
  expect(screen.getByText('Mail message')).toBeInTheDocument()
  expect(screen.getByText('25')).toBeInTheDocument()
})
it('explains an empty inventory and links to Sources', async () => {
  apiCall.mockResolvedValue({ ok: true, json: async () => ({ ...overview, total: 0, by_type: [] }) })
  renderWithRouter(<IngestionOverview />)
  expect(await screen.findByText('No items ingested yet')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Connect a source' })).toHaveAttribute('href', '/ui/sources')
})
it('shows a load failure instead of pretending there are zero items', async () => {
  apiCall.mockRejectedValue(new Error('Unavailable'))
  renderWithRouter(<IngestionOverview />)
  expect(await screen.findByText('Unavailable')).toBeInTheDocument()
  expect(screen.queryByText('No items ingested yet')).not.toBeInTheDocument()
})

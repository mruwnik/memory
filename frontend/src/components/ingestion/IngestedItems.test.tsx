import { useNavigate } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import { renderWithRouter, screen, waitFor, within } from '@/test/utils'
import IngestedItems from './IngestedItems'

const listItems = vi.fn()
const countItems = vi.fn()
vi.mock('@/hooks/useIngestedItems', () => ({ useIngestedItems: () => ({ listItems, countItems }) }))

const mail = (id: number, title: string, extra = {}) => ({
  id, type: 'mail_message', modality: 'mail', embed_status: 'STORED', title, filename: null,
  mime_type: 'message/rfc822', size: 2048, inserted_at: '2026-11-01T12:00:00Z', preview: `Body of ${title}`, ...extra,
})

const connections = [
  { id: '11', name: 'work@example.com', count: 30 },
  { id: '12', name: 'home@example.com', count: 4 },
]

beforeEach(() => {
  vi.clearAllMocks()
  listItems.mockResolvedValue({ items: [mail(1, 'Invoice'), mail(2, 'Lunch', { embed_status: 'FAILED' })], hasMore: true })
  countItems.mockImplementation(async ({ itemTypes = [] } = {}) => ({
    total: 34,
    byType: { mail_message: 34, misc_doc: 2 },
    byConnection: itemTypes.length ? connections : null,
  }))
})

const lastQuery = () => listItems.mock.calls.at(-1)

it('lists a type newest first with each item status', async () => {
  renderWithRouter(<IngestedItems />, { initialEntries: ['/ingestion/items?type=mail_message'] })

  expect(await screen.findByText('Invoice')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Mail message' })).toBeInTheDocument()
  expect(screen.getByText('indexing failed')).toBeInTheDocument()
  expect(screen.getAllByText('2.0 KB')).toHaveLength(2)
  expect(lastQuery()).toEqual([
    { itemTypes: ['mail_message'], statuses: undefined, contains: '', connectionId: '' },
    { limit: 50, offset: 0 },
  ])
})

it('shows an item preview when its row is opened', async () => {
  const { user } = renderWithRouter(<IngestedItems />, { initialEntries: ['/ingestion/items?type=mail_message'] })

  await user.click(await screen.findByRole('button', { name: 'Invoice' }))

  expect(screen.getByText('Body of Invoice')).toBeInTheDocument()
})

it('searches as you type, from the first page', async () => {
  const { user } = renderWithRouter(<IngestedItems />, { initialEntries: ['/ingestion/items?type=mail_message&page=2'] })
  await screen.findByText('Invoice')

  await user.type(screen.getByRole('searchbox', { name: 'Search items' }), 'invoice')

  await waitFor(() => expect(lastQuery()).toEqual([
    { itemTypes: ['mail_message'], statuses: undefined, contains: 'invoice', connectionId: '' },
    { limit: 50, offset: 0 },
  ]))
})

const BackButton = () => {
  const navigate = useNavigate()
  return <button onClick={() => navigate(-1)}>Back</button>
}

it('shows the search of the page that history returns to', async () => {
  const { user } = renderWithRouter(<><IngestedItems /><BackButton /></>, { initialEntries: ['/ingestion/items?type=mail_message'] })
  await screen.findByText('Invoice')
  const box = screen.getByRole('searchbox', { name: 'Search items' })
  await user.type(box, 'a')
  await waitFor(() => expect(lastQuery()?.[0]).toMatchObject({ contains: 'a' }))
  await user.click(screen.getByRole('button', { name: 'Next' }))
  await user.clear(box)
  await user.type(box, 'b')
  await waitFor(() => expect(lastQuery()?.[0]).toMatchObject({ contains: 'b' }))

  await user.click(screen.getByRole('button', { name: 'Back' }))

  await waitFor(() => expect(lastQuery()).toEqual([
    { itemTypes: ['mail_message'], statuses: undefined, contains: 'a', connectionId: '' },
    { limit: 50, offset: 0 },
  ]))
  expect(box).toHaveValue('a')
})

it('keeps what you type once the search runs', async () => {
  const { user } = renderWithRouter(<IngestedItems />, { initialEntries: ['/ingestion/items'] })
  await screen.findByText('Invoice')
  const box = screen.getByRole('searchbox', { name: 'Search items' })

  await user.type(box, 'lunch ')

  await waitFor(() => expect(lastQuery()?.[0]).toMatchObject({ contains: 'lunch' }))
  expect(box).toHaveValue('lunch ')
})

it('narrows to one connection of the type', async () => {
  const { user } = renderWithRouter(<IngestedItems />, { initialEntries: ['/ingestion/items?type=mail_message'] })
  const picker = await screen.findByRole('combobox', { name: 'Connection' })

  expect(within(picker).getByRole('option', { name: 'work@example.com (30)' })).toBeInTheDocument()
  await user.selectOptions(picker, '12')

  await waitFor(() => expect(lastQuery()?.[0]).toMatchObject({ connectionId: '12' }))
  expect(screen.getByText(/From home@example.com\./)).toBeInTheDocument()
})

it('offers no connection filter for all types', async () => {
  renderWithRouter(<IngestedItems />, { initialEntries: ['/ingestion/items'] })

  expect(await screen.findByText('Invoice')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'All items' })).toBeInTheDocument()
  expect(screen.getByRole('option', { name: 'Misc doc' })).toBeInTheDocument()
  expect(screen.queryByRole('combobox', { name: 'Connection' })).not.toBeInTheDocument()
  // A type badge on each row.
  expect(screen.getAllByText('Mail message', { selector: 'span' })).toHaveLength(2)
})

it.each([
  ['Awaiting indexing', ['RAW', 'QUEUED']],
  ['Failed', ['FAILED']],
])('filters by the %s status', async (label, statuses) => {
  const { user } = renderWithRouter(<IngestedItems />, { initialEntries: ['/ingestion/items'] })
  await screen.findByText('Invoice')

  await user.click(screen.getByRole('button', { name: label }))

  await waitFor(() => expect(lastQuery()?.[0]).toMatchObject({ statuses }))
})

it('pages forward by offset', async () => {
  const { user } = renderWithRouter(<IngestedItems />, { initialEntries: ['/ingestion/items'] })
  await screen.findByText('Invoice')

  await user.click(screen.getByRole('button', { name: 'Next' }))

  await waitFor(() => expect(lastQuery()?.[1]).toEqual({ limit: 50, offset: 50 }))
  expect(screen.getByText('Page 2')).toBeInTheDocument()
})

it.each([
  ['/ingestion/items?type=mail_message', 'No items you can see yet'],
  ['/ingestion/items?type=mail_message&q=zzz', 'No items match these filters'],
])('explains an empty list at %s', async (entry, text) => {
  listItems.mockResolvedValue({ items: [], hasMore: false })
  renderWithRouter(<IngestedItems />, { initialEntries: [entry] })

  expect(await screen.findByText(text)).toBeInTheDocument()
  expect(screen.queryByRole('navigation', { name: 'Pagination' })).not.toBeInTheDocument()
})

it('shows a load failure with a retry', async () => {
  listItems.mockRejectedValueOnce(new Error('MCP core_list_items failed'))
  const { user } = renderWithRouter(<IngestedItems />, { initialEntries: ['/ingestion/items'] })

  expect(await screen.findByText('MCP core_list_items failed')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Retry' }))

  expect(await screen.findByText('Invoice')).toBeInTheDocument()
})

it('still lists items when the filter counts fail', async () => {
  countItems.mockRejectedValue(new Error('slow'))
  renderWithRouter(<IngestedItems />, { initialEntries: ['/ingestion/items?type=mail_message'] })

  expect(await screen.findByText('Invoice')).toBeInTheDocument()
  expect(screen.queryByRole('combobox', { name: 'Connection' })).not.toBeInTheDocument()
})

it('shows a loading state before the first page arrives', () => {
  listItems.mockReturnValue(new Promise(() => {}))
  renderWithRouter(<IngestedItems />, { initialEntries: ['/ingestion/items'] })

  expect(screen.getByText('Loading items...')).toBeInTheDocument()
})

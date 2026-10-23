import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, renderWithRouter, screen, waitFor } from '@/test/utils'
import { emptyTriggerConfig } from '@/hooks/useAgents'
import { Triggers, describeTrigger } from './Triggers'

const listTriggers = vi.fn()
const saveTrigger = vi.fn()
const deleteTrigger = vi.fn()

vi.mock('@/hooks/useAgents', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAgents')>()),
  useAgents: () => ({ listTriggers, saveTrigger, deleteTrigger }),
}))

const config = {
  ...emptyTriggerConfig(),
  filter: { ...emptyTriggerConfig().filter, tags_any: ['ai'] },
}

beforeEach(() => {
  listTriggers.mockReset()
  saveTrigger.mockReset()
  deleteTrigger.mockReset()
})

describe('Triggers', () => {
  it('summarises a filter and its delivery', () => {
    expect(describeTrigger({ ...config, delivery: { mode: 'batch', window_sec: 600, max_items: 20 } }))
      .toBe('tags: ai · batched (20 items or 600s)')
  })

  it('shows why a tripped trigger was paused, and re-enables it', async () => {
    listTriggers.mockResolvedValue([
      { id: 3, definition_id: 1, kind: 'ingest', config, enabled: false, state: { tripped: { reason: 'definition rate: 5 runs', at: 'x' } } },
    ])
    saveTrigger.mockResolvedValue({})
    renderWithRouter(<Triggers definitionId={1} sourceTypes={['blog_post']} />)

    await waitFor(() => expect(screen.getByText(/Tripped: definition rate/)).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Enable' }))

    await waitFor(() => expect(saveTrigger).toHaveBeenCalledWith({ definitionId: 1, enabled: true, triggerId: 3 }))
  })

  it('stops showing loading when listing fails', async () => {
    listTriggers.mockRejectedValue(new Error('boom'))
    renderWithRouter(<Triggers definitionId={1} sourceTypes={[]} />)

    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument())
    expect(screen.queryByText('Loading triggers...')).not.toBeInTheDocument()
  })

  it('creates a trigger from the form', async () => {
    listTriggers.mockResolvedValue([])
    saveTrigger.mockResolvedValue({})
    renderWithRouter(<Triggers definitionId={1} sourceTypes={['blog_post']} />)

    await waitFor(() => expect(screen.getByText('No triggers.')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'New trigger' }))
    fireEvent.change(screen.getByLabelText(/Any of these tags/), { target: { value: 'ai, ml' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save trigger' }))

    await waitFor(() => expect(saveTrigger).toHaveBeenCalledWith({
      definitionId: 1,
      config: { ...emptyTriggerConfig(), filter: { ...emptyTriggerConfig().filter, tags_any: ['ai', 'ml'] } },
    }))
  })
})

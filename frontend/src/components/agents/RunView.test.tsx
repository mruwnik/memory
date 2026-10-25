import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderWithRouter, screen, waitFor, within } from '@/test/utils'
import { StrictMode } from 'react'
import { Routes, Route } from 'react-router-dom'
import { MCPError } from '@/hooks/useMCP'
import { RunView } from './index'

const waitRun = vi.fn()
const cancelRun = vi.fn()
const starRun = vi.fn()
const getDefinition = vi.fn()

vi.mock('@/hooks/useAgents', async (orig) => ({
  ...(await orig<typeof import('@/hooks/useAgents')>()),
  useAgents: () => ({ waitRun, cancelRun, starRun, getDefinition }),
}))

const run = (o = {}) => ({
  id: 'r1', definition_id: null, input: 'do it', status: 'running', result: null, error: null,
  usage: { input_tokens: 100, output_tokens: 10 }, cost_usd: '0.000120', max_cost_usd: '1.000000',
  num_turns: 1, trigger_kind: 'manual', starred: false, model: 'anthropic/claude-sonnet-5-5',
  created_at: null, started_at: null, finished_at: null, steps_purged_at: null, ...o,
})

const view = (path = '/ui/agents/runs/r1') => renderWithRouter(
  <Routes><Route path="/ui/agents/runs/:runId" element={<RunView />} /></Routes>,
  { initialEntries: [path] },
)

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  waitRun.mockReset()
  cancelRun.mockReset()
  starRun.mockReset()
  getDefinition.mockReset().mockResolvedValue({ id: 3, name: 'digest' })
})

afterEach(() => vi.useRealTimers())

describe('RunView', () => {
  it('renders tool calls and results from the transcript', async () => {
    waitRun.mockResolvedValueOnce({
      run: run({ status: 'done', result: { text: 'answer' } }),
      steps: [
        { seq: 0, kind: 'user', content: [{ type: 'text', text: 'do it' }], usage: null, cost_usd: null, created_at: null },
        { seq: 1, kind: 'assistant', content: [{ type: 'tool_call', id: 't1', name: 'core_search', input: { q: 'x' }, executed_by: 'runner' }], usage: null, cost_usd: '0.0001', created_at: null },
        { seq: 2, kind: 'tool_results', content: [{ type: 'tool_result', tool_call_id: 't1', name: 'core_search', text: 'found it', is_error: false, duration_ms: 12 }], usage: null, cost_usd: null, created_at: null },
      ],
    })
    view()
    await waitFor(() => expect(screen.getByText('found it')).toBeInTheDocument())
    expect(screen.getAllByText('core_search').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: 'Cancel run' })).not.toBeInTheDocument()
  })

  it('renders model output as text, never as markup', async () => {
    waitRun.mockResolvedValueOnce({
      run: run({ status: 'done' }),
      steps: [{ seq: 0, kind: 'assistant', content: [{ type: 'text', text: '<img src=x onerror=alert(1)>**bold**' }], usage: null, cost_usd: null, created_at: null }],
    })
    const { container } = view()
    await waitFor(() => expect(screen.getByText('<img src=x onerror=alert(1)>**bold**')).toBeInTheDocument())
    expect(container.querySelector('img')).toBeNull()
  })

  it('polls for new steps while running, from the last seq seen', async () => {
    waitRun
      .mockResolvedValueOnce({ run: run(), steps: [{ seq: 0, kind: 'user', content: [{ type: 'text', text: 'do it' }], usage: null, cost_usd: null, created_at: null }] })
      .mockResolvedValueOnce({ run: run({ status: 'done', result: { text: 'ok' } }), steps: [] })
    view()
    await waitFor(() => expect(waitRun).toHaveBeenCalledTimes(1))
    vi.advanceTimersByTime(2100)
    await waitFor(() => expect(waitRun).toHaveBeenCalledTimes(2))
    expect(waitRun).toHaveBeenLastCalledWith('r1', 0, 0, 100)
  })

  it('shows each step once when polls overlap', async () => {
    const page = {
      run: run({ status: 'done' }),
      steps: [
        { seq: 0, kind: 'user', content: [{ type: 'text', text: 'first step' }], usage: null, cost_usd: null, created_at: null },
        { seq: 1, kind: 'assistant', content: [{ type: 'text', text: 'second step' }], usage: null, cost_usd: null, created_at: null },
      ],
    }
    // StrictMode mounts effects twice, so two polls race for the same page.
    waitRun.mockResolvedValue(page)
    renderWithRouter(
      <StrictMode><Routes><Route path="/ui/agents/runs/:runId" element={<RunView />} /></Routes></StrictMode>,
      { initialEntries: ['/ui/agents/runs/r1'] },
    )
    await waitFor(() => expect(screen.getByText('second step')).toBeInTheDocument())
    expect(screen.getAllByText('first step')).toHaveLength(1)
    expect(screen.getAllByText('second step')).toHaveLength(1)
  })

  it('keeps polling through transient errors', async () => {
    waitRun
      .mockRejectedValueOnce(new Error('MCP agent_wait failed (502)'))
      .mockResolvedValueOnce({ run: run({ status: 'done', result: { text: 'recovered' } }), steps: [] })
    view()
    await waitFor(() => expect(screen.getByText(/502/)).toBeInTheDocument())
    vi.advanceTimersByTime(4100)
    await waitFor(() => expect(screen.getByText('recovered')).toBeInTheDocument())
  })

  it('says when the transcript was deleted by retention', async () => {
    waitRun.mockResolvedValueOnce({ run: run({ status: 'done', steps_purged_at: '2026-01-01T00:00:00Z', result: { text: 'kept' } }), steps: [] })
    view()
    await waitFor(() => expect(screen.getByText(/transcript deleted after 90 days/i)).toBeInTheDocument())
    expect(screen.getByText('kept')).toBeInTheDocument()
  })

  it('names the agent and shows what was asked, when and how', async () => {
    waitRun.mockResolvedValueOnce({
      run: run({
        status: 'done', definition_id: 3, trigger_kind: 'cron', usage: { input_tokens: 12345, output_tokens: 1500 },
        started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:02:05Z',
      }),
      steps: [],
    })
    view()
    expect(await screen.findByRole('heading', { name: /digest/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'digest' })).toHaveAttribute('href', '/ui/agents/3')
    expect(screen.getByText('do it')).toBeInTheDocument()
    expect(screen.getByText('Schedule')).toBeInTheDocument()
    expect(screen.getByText('2m 5s')).toBeInTheDocument()
    expect(screen.getByText(/12,345 context/)).toBeInTheDocument()
  })

  it.each([
    ['/ui/agents/runs/r1', 3, 'Runs', '/ui/agents/runs'],
    ['/ui/agents/runs/r1?from=agent', 3, 'digest', '/ui/agents/3'],
    ['/ui/agents/runs/r1?from=agent', null, 'Runs', '/ui/agents/runs'],
  ])('from %s, a run of agent %s goes back to %s', async (path, definitionId, label, href) => {
    waitRun.mockResolvedValueOnce({ run: run({ status: 'done', definition_id: definitionId }), steps: [] })
    view(path)
    await screen.findByRole('heading', { name: /digest|Ad-hoc/ })
    // The back link comes first, above the run's details.
    await waitFor(() => expect(screen.getAllByRole('link', { name: label })[0]).toHaveAttribute('href', href))
  })

  it('titles a run without an agent as ad-hoc', async () => {
    waitRun.mockResolvedValueOnce({ run: run({ status: 'done' }), steps: [] })
    view()
    expect(await screen.findByRole('heading', { name: /Ad-hoc run/ })).toBeInTheDocument()
    expect(getDefinition).not.toHaveBeenCalled()
  })

  it('asks before cancelling a run', async () => {
    waitRun.mockResolvedValue({ run: run(), steps: [] })
    cancelRun.mockResolvedValue(run({ status: 'cancelled' }))
    const { user } = view()
    await user.click(await screen.findByRole('button', { name: 'Cancel run' }))
    expect(cancelRun).not.toHaveBeenCalled()
    expect(within(screen.getByRole('dialog')).getByRole('button', { name: 'Keep running' })).toBeInTheDocument()
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel run' }))
    expect(cancelRun).toHaveBeenCalledWith('r1')
  })

  it('keeps an action error while the run keeps polling', async () => {
    waitRun.mockResolvedValue({ run: run(), steps: [] })
    starRun.mockRejectedValue(new Error('star failed'))
    const { user } = view()
    await user.click(await screen.findByRole('button', { name: 'Star' }))
    expect(await screen.findByText('star failed')).toBeInTheDocument()
    vi.advanceTimersByTime(2100)
    await waitFor(() => expect(waitRun).toHaveBeenCalledTimes(2))
    expect(screen.getByText('star failed')).toBeInTheDocument()
  })

  it('keeps one star label and reports the state with aria-pressed', async () => {
    waitRun.mockResolvedValueOnce({ run: run({ status: 'done', starred: true }), steps: [] })
    view()
    expect(await screen.findByRole('button', { name: 'Star' })).toHaveAttribute('aria-pressed', 'true')
  })

  it.each([
    ['an unknown run', new Error('Error calling tool agent_wait: unknown run r1')],
    ['an access denial', new MCPError('You may not see this', undefined, true)],
    ['an HTTP 404', new MCPError('MCP call agent_wait failed (404) Not Found', 404)],
    ['an HTTP 403', new MCPError('MCP call agent_wait failed (403) Forbidden', 403)],
  ])('stops polling after %s, and offers a retry', async (_, error) => {
    waitRun.mockRejectedValue(error)
    const { user } = view()
    await user.click(await screen.findByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(waitRun).toHaveBeenCalledTimes(2))
    vi.advanceTimersByTime(35_000)
    expect(waitRun).toHaveBeenCalledTimes(2)
  })
})

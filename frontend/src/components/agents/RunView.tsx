import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { TERMINAL_STATUSES, useAgents, type AgentRun, type Block, type RunStep } from '@/hooks/useAgents'
import { StatusBadge, TRIGGER_LABELS } from './StatusBadge'
import { Badge, Button, ConfirmButton, DetailList, Disclosure, List, Notice, Page, Row, Section, useActionNotice, type Tone } from '@/components/shared/ui'
import { formatCost } from '@/utils/format'

const POLL_MS = 2000
const PAGE_SIZE = 100
const MAX_RETRY_MS = 30_000
const LONG_INPUT = 200
// agent_wait reports a run you can't see as unknown; useMCP turns access denials into a permission message.
const NOT_FOUND = /unknown run|permission|failed \(40[34]\)/i

const formatDuration = (start: string, end: string) => {
  const seconds = Math.round((Date.parse(end) - Date.parse(start)) / 1000)
  const [h, m, s] = [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60]
  return h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`
}

// Model and tool output is untrusted: it is only ever rendered as plain text.
function BlockView({ block }: { block: Block }) {
  switch (block.type) {
    case 'text':
      return <div className="whitespace-pre-wrap">{block.text}</div>
    case 'reasoning':
      return (
        <details className="text-slate-600 text-sm">
          <summary>reasoning</summary>
          <div className="whitespace-pre-wrap">{block.text}</div>
        </details>
      )
    case 'tool_call':
      return (
        <div className="text-sm">
          <span className="font-mono text-primary-dark">{block.name}</span>
          {block.executed_by === 'provider' && <span className="ml-2 text-xs text-slate-600">(run by provider)</span>}
          <pre className="bg-slate-50 p-2 rounded text-xs overflow-x-auto">{JSON.stringify(block.input, null, 2)}</pre>
        </div>
      )
    case 'tool_result':
      return (
        <details className={`text-sm ${block.is_error ? 'text-red-700' : ''}`} open={block.text.length < 500}>
          <summary className="font-mono">
            {block.name} → {block.is_error ? 'error' : 'result'}{block.duration_ms != null ? ` (${block.duration_ms} ms)` : ''}
          </summary>
          <pre className="bg-slate-50 p-2 rounded text-xs whitespace-pre-wrap">{block.text}</pre>
        </details>
      )
  }
}

const contextTokens = (usage: Record<string, number>) =>
  (usage.input_tokens ?? 0) + (usage.cache_read_tokens ?? 0) + (usage.cache_write_tokens ?? 0)

const KIND_TONES: Record<RunStep['kind'], Tone> = {
  user: 'neutral',
  assistant: 'primary',
  tool_results: 'info',
  notice: 'warning',
}

/** Steps not already held: overlapping polls can return the same page twice. */
const appendNew = (held: RunStep[], fresh: RunStep[]) => {
  const last = held.length ? held[held.length - 1].seq : -1
  return [...held, ...fresh.filter(s => s.seq > last)]
}

export default function RunViewPage() {
  const { runId = '' } = useParams()
  // A new run id starts from a clean view rather than appending to the old one.
  return <RunView key={runId} runId={runId} />
}

function RunView({ runId }: { runId: string }) {
  const { waitRun, cancelRun, starRun, getDefinition } = useAgents()
  // Agent pages link here with ?from=agent, so Back returns to the agent rather than all runs.
  const fromAgent = useSearchParams()[0].get('from') === 'agent'
  const [run, setRun] = useState<AgentRun | null>(null)
  const [steps, setSteps] = useState<RunStep[]>([])
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [agentName, setAgentName] = useState<string | null>(null)
  const { run: act, notice } = useActionNotice()

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    let stopped = false
    let lastSeq = -1
    let failures = 0
    const tick = async () => {
      let delay = POLL_MS
      try {
        const { run: latest, steps: fresh } = await waitRun(runId, lastSeq, 0, PAGE_SIZE)
        if (stopped) return
        if (fresh.length) lastSeq = Math.max(lastSeq, fresh[fresh.length - 1].seq)
        setSteps(held => appendNew(held, fresh))
        setRun(latest)
        setError(null)
        failures = 0
        if (fresh.length === PAGE_SIZE) delay = 0
        else if (TERMINAL_STATUSES.includes(latest.status)) return
      } catch (e) {
        if (stopped) return
        const message = (e as Error).message
        setError(message)
        // A missing or forbidden run won't appear by waiting; anything else may be transient, so back off and retry.
        if (NOT_FOUND.test(message)) return
        failures += 1
        delay = Math.min(POLL_MS * 2 ** failures, MAX_RETRY_MS)
      }
      if (!stopped) timer = setTimeout(tick, delay)
    }
    tick()
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [runId, waitRun, attempt])

  const definitionId = run?.definition_id ?? null
  useEffect(() => {
    if (definitionId === null) return
    // The name is a nicety: without it the run is titled by the agent's id.
    getDefinition(definitionId).then(d => setAgentName(d.name)).catch(() => {})
  }, [definitionId, getDefinition])

  const retry = () => setAttempt(n => n + 1)
  const runs = { to: '/ui/agents/runs', label: 'Runs' }
  if (!run) return <Page back={runs} title="Run" loading="Loading run..." error={error} onRetry={retry} />
  const live = !TERMINAL_STATUSES.includes(run.status)
  const agent = run.definition_id === null ? null : agentName ?? `Agent ${run.definition_id}`
  const back = agent && fromAgent ? { to: `/ui/agents/${run.definition_id}`, label: agent } : runs

  return (
    <Page
      back={back}
      title={<>{agent ?? 'Ad-hoc run'} <StatusBadge status={run.status} /></>}
      actions={<>
        <Button aria-pressed={run.starred} onClick={() => act(async () => setRun(await starRun(run.id, !run.starred)))}>
          <span aria-hidden="true">{run.starred ? '★' : '☆'}</span> Star
        </Button>
        {live && (
          <ConfirmButton size="md" confirm="Cancel this run? It stops now and can't be resumed." confirmLabel="Cancel run" cancelLabel="Keep running"
            onConfirm={() => act(async () => setRun(await cancelRun(run.id)))}>Cancel run</ConfirmButton>
        )}
      </>}
    >
    <DetailList items={[
      ['Agent', agent && <Link to={`/ui/agents/${run.definition_id}`} className="text-primary hover:underline">{agent}</Link>],
      ['Input', run.input.length > LONG_INPUT || run.input.includes('\n')
        ? <Disclosure summary="Show input"><div className="whitespace-pre-wrap">{run.input}</div></Disclosure>
        : run.input],
      ['Model', <span className="font-mono">{run.model}</span>],
      ['Trigger', TRIGGER_LABELS[run.trigger_kind] ?? run.trigger_kind],
      ['Started', run.started_at && new Date(run.started_at).toLocaleString()],
      ['Duration', run.started_at && run.finished_at && formatDuration(run.started_at, run.finished_at)],
      ['Cost', `${formatCost(run.cost_usd)} of ${formatCost(run.max_cost_usd)}`],
      ['Turns', run.num_turns],
      ['Tokens', `${contextTokens(run.usage).toLocaleString()} context (${(run.usage.cache_read_tokens ?? 0).toLocaleString()} cached) / ${(run.usage.output_tokens ?? 0).toLocaleString()} out`],
    ]} />
    {notice}
    {error && <Notice tone="error" onRetry={retry}>{error}</Notice>}
    {run.error && <Notice tone="error">{run.error}</Notice>}
    {run.result && (
      <Notice tone="success">
        {typeof run.result.text === 'string' ? run.result.text : JSON.stringify(run.result, null, 2)}
      </Notice>
    )}
    <Section title="Transcript"
      description={run.steps_purged_at && 'Transcript deleted after 90 days (star a run to keep its transcript).'}>
      {steps.length > 0 && (
        <List>
          {steps.map(step => (
            <Row key={step.seq} title={`#${step.seq}`} badges={<Badge tone={KIND_TONES[step.kind]}>{step.kind}</Badge>}
              meta={[step.cost_usd && formatCost(step.cost_usd)]}>
              {step.content.map((block, i) => <BlockView key={i} block={block} />)}
            </Row>
          ))}
        </List>
      )}
    </Section>
    {live && <div role="status" className="text-sm text-slate-600">running…</div>}
  </Page>
)
}

import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { TERMINAL_STATUSES, useAgents, type AgentRun, type Block, type RunStep } from '@/hooks/useAgents'
import { StatusBadge } from './StatusBadge'
import { Badge, Button, DetailList, List, Notice, Page, Row, Section, type Tone } from '@/components/shared/ui'
import { formatCost } from '@/utils/format'

const POLL_MS = 2000
const PAGE_SIZE = 100
const MAX_RETRY_MS = 30_000

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
  const { waitRun, cancelRun, starRun } = useAgents()
  const [run, setRun] = useState<AgentRun | null>(null)
  const [steps, setSteps] = useState<RunStep[]>([])
  const [error, setError] = useState<string | null>(null)

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
        // Keep following the run through transient failures, backing off.
        setError((e as Error).message)
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
  }, [runId, waitRun])

  const act = async (fn: () => Promise<AgentRun>) => {
    try {
      setRun(await fn())
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const back = { to: '/ui/agents/runs', label: 'Runs' }
  if (!run) return <Page back={back} title="Run" loading="Loading run..." error={error} />
  const live = !TERMINAL_STATUSES.includes(run.status)

  return (
    <Page
      back={back}
      title={<>Run <StatusBadge status={run.status} /></>}
      actions={<>
        <Button aria-pressed={run.starred} onClick={() => act(() => starRun(run.id, !run.starred))}>
          {run.starred ? '★ Starred' : '☆ Star'}
        </Button>
        {live && <Button variant="danger" onClick={() => act(() => cancelRun(run.id))}>Cancel run</Button>}
      </>}
    >
    <DetailList items={[
      ['Model', <span className="font-mono">{run.model}</span>],
      ['Cost', `${formatCost(run.cost_usd)} of ${formatCost(run.max_cost_usd)}`],
      ['Turns', run.num_turns],
      ['Tokens', `${contextTokens(run.usage)} context (${run.usage.cache_read_tokens ?? 0} cached) / ${run.usage.output_tokens ?? 0} out`],
      ['Trigger', run.trigger_kind],
    ]} />
    {error && <Notice tone="error">{error}</Notice>}
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
              meta={[step.cost_usd && `$${step.cost_usd}`]}>
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

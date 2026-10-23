import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { TERMINAL_STATUSES, useAgents, type AgentRun, type Block, type RunStep } from '@/hooks/useAgents'
import { StatusBadge } from './StatusBadge'

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
        <details className="text-slate-500 text-sm">
          <summary>reasoning</summary>
          <div className="whitespace-pre-wrap">{block.text}</div>
        </details>
      )
    case 'tool_call':
      return (
        <div className="text-sm">
          <span className="font-mono text-indigo-700">{block.name}</span>
          {block.executed_by === 'provider' && <span className="ml-2 text-xs text-slate-400">(run by provider)</span>}
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

const KIND_STYLE: Record<RunStep['kind'], string> = {
  user: 'border-l-4 border-slate-300',
  assistant: 'border-l-4 border-indigo-300',
  tool_results: 'border-l-4 border-teal-300',
  notice: 'border-l-4 border-amber-300 text-amber-800 text-sm',
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

  if (!run) return <div className="p-6 text-slate-500">{error ?? 'Loading run...'}</div>
  const live = !TERMINAL_STATUSES.includes(run.status)

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-4">
      <Link to="/ui/agents/runs" className="text-sm text-primary">← Runs</Link>
      <div className="bg-white rounded-xl shadow-sm p-4 flex flex-wrap items-center gap-4 text-sm">
        <StatusBadge status={run.status} />
        <span className="font-mono">{run.model}</span>
        <span>${run.cost_usd} of ${run.max_cost_usd}</span>
        <span>{run.num_turns} turns</span>
        <span>
          {contextTokens(run.usage)} context ({run.usage.cache_read_tokens ?? 0} cached) / {run.usage.output_tokens ?? 0} out tokens
        </span>
        <span className="text-slate-400">trigger: {run.trigger_kind}</span>
        <button className="ml-auto px-2 py-1 border rounded" onClick={() => act(() => starRun(run.id, !run.starred))}>
          {run.starred ? '★ Starred' : '☆ Star'}
        </button>
        {live && (
          <button className="px-2 py-1 border border-red-300 text-red-700 rounded" onClick={() => act(() => cancelRun(run.id))}>
            Cancel
          </button>
        )}
      </div>
      {error && <div className="p-3 bg-red-50 text-red-700 rounded text-sm">{error}</div>}
      {run.error && <div className="p-3 bg-red-50 text-red-700 rounded text-sm whitespace-pre-wrap">{run.error}</div>}
      {run.result && (
        <div className="p-3 bg-green-50 rounded text-sm whitespace-pre-wrap">
          {typeof run.result.text === 'string' ? run.result.text : JSON.stringify(run.result, null, 2)}
        </div>
      )}
      {run.steps_purged_at && <div className="text-sm text-slate-500">Transcript deleted after 90 days (star a run to keep its transcript).</div>}
      <ol className="space-y-2">
        {steps.map(step => (
          <li key={step.seq} className={`bg-white rounded p-3 space-y-2 ${KIND_STYLE[step.kind]}`}>
            <div className="text-xs text-slate-400">#{step.seq} {step.kind}{step.cost_usd ? ` · $${step.cost_usd}` : ''}</div>
            {step.content.map((block, i) => <BlockView key={i} block={block} />)}
          </li>
        ))}
      </ol>
      {live && <div className="text-sm text-slate-400">running…</div>}
    </div>
  )
}

import { useCallback, useEffect, useState } from 'react'
import { useAgents, type AgentSchedule } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'

export function Schedules({ definitionId }: { definitionId: number }) {
  const { listSchedules, saveSchedule, deleteSchedule } = useAgents()
  const [schedules, setSchedules] = useState<AgentSchedule[] | null>(null)
  const [cron, setCron] = useState('0 9 * * *')
  const [input, setInput] = useState('')
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(
    () => listSchedules(definitionId).then(setSchedules).catch(e => setError(e.message)),
    [listSchedules, definitionId],
  )
  useEffect(() => { reload() }, [reload])

  const act = async (action: () => Promise<unknown>) => {
    setError(null)
    try {
      await action()
      await reload()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const add = () => act(async () => {
    await saveSchedule({ definitionId, input, cron })
    setInput('')
  })

  const remove = (s: AgentSchedule) => {
    if (!window.confirm('Delete this schedule and its history?')) return
    act(() => deleteSchedule(s.id))
  }

  return (
    <section className="space-y-3">
      <h2 className="font-semibold">Schedules</h2>
      {error && <div className="p-3 bg-red-50 text-red-700 rounded text-sm whitespace-pre-wrap">{error}</div>}
      {schedules === null && !error && <p className="text-sm text-slate-500">Loading schedules...</p>}
      {schedules?.length === 0 && <p className="text-sm text-slate-500">Not scheduled.</p>}
      {!!schedules?.length && (
        <ul className="divide-y bg-white rounded-xl shadow-sm">
          {schedules.map(s => (
            <li key={s.id} className="p-3 flex justify-between items-center gap-3 text-sm">
              <span className="min-w-0">
                <code className="mr-2">{s.cron_expression ?? 'once'}</code>
                <span className="text-slate-600 truncate">{s.data.input}</span>
                <span className="block text-xs text-slate-400">
                  {s.enabled ? `next ${formatRelativeTime(s.next_scheduled_time)}` : s.cron_expression ? 'paused' : 'done'}
                </span>
              </span>
              <span className="flex gap-2 shrink-0">
                {(s.enabled || s.cron_expression) && (
                  <button className="px-2 py-1 border rounded text-xs"
                    onClick={() => act(() => saveSchedule({ definitionId, enabled: !s.enabled, taskId: s.id }))}>
                    {s.enabled ? 'Pause' : 'Resume'}
                  </button>
                )}
                <button className="px-2 py-1 border border-red-300 text-red-700 rounded text-xs"
                  onClick={() => remove(s)}>Delete</button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-2 items-start">
        <input aria-label="Cron expression" className="px-3 py-1.5 border rounded w-36 font-mono text-sm"
          value={cron} onChange={e => setCron(e.target.value)} />
        <textarea aria-label="Scheduled input" className="flex-1 px-3 py-1.5 border rounded h-16 text-sm"
          placeholder="input for each run" value={input} onChange={e => setInput(e.target.value)} />
        <button className="px-3 py-1.5 bg-primary text-white rounded text-sm disabled:opacity-50"
          disabled={!input.trim() || !cron.trim()} onClick={add}>Schedule</button>
      </div>
      <p className="text-xs text-slate-500">
        Cron is in UTC. Each run uses the saved version of this agent.
      </p>
    </section>
  )
}

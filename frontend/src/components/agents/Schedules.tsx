import { useCallback, useEffect, useState } from 'react'
import { useAgents, type AgentSchedule } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { Button, Input, List, ListItem, Notice, Section, Textarea } from '@/components/shared/ui'

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
    <Section title="Schedules" description="Cron is in UTC. Each run uses the saved version of this agent.">
      {error && <Notice tone="error">{error}</Notice>}
      {schedules === null && !error && <p className="text-sm text-slate-600">Loading schedules...</p>}
      {schedules?.length === 0 && <p className="text-sm text-slate-600">Not scheduled.</p>}
      {!!schedules?.length && (
        <List>
          {schedules.map(s => (
            <ListItem key={s.id}>
              <span className="min-w-0">
                <code className="mr-2">{s.cron_expression ?? 'once'}</code>
                <span className="text-slate-700 break-words">{s.data.input}</span>
                <span className="block text-xs text-slate-600">
                  {s.enabled ? `next ${formatRelativeTime(s.next_scheduled_time)}` : s.cron_expression ? 'paused' : 'done'}
                </span>
              </span>
              <span className="flex gap-2 shrink-0">
                {(s.enabled || s.cron_expression) && (
                  <Button size="sm"
                    onClick={() => act(() => saveSchedule({ definitionId, enabled: !s.enabled, taskId: s.id }))}>
                    {s.enabled ? 'Pause' : 'Resume'}
                  </Button>
                )}
                <Button variant="danger" size="sm" onClick={() => remove(s)}>Delete</Button>
              </span>
            </ListItem>
          ))}
        </List>
      )}
      <div className="flex flex-col sm:flex-row gap-2 sm:items-start">
        <Input aria-label="Cron expression" className="sm:w-36 font-mono"
          value={cron} onChange={e => setCron(e.target.value)} />
        <Textarea aria-label="Scheduled input" className="flex-1 h-16"
          placeholder="input for each run" value={input} onChange={e => setInput(e.target.value)} />
        <Button variant="primary" disabled={!input.trim() || !cron.trim()} onClick={add}>Schedule</Button>
      </div>
    </Section>
  )
}

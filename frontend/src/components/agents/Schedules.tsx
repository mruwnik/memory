import { useCallback, useEffect, useState } from 'react'
import { useAgents, type AgentSchedule } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { Badge, Button, ConfirmButton, Input, List, Notice, Row, Section, Textarea } from '@/components/shared/ui'

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

  return (
    <Section title="Schedules" description="Cron is in UTC. Each run uses the saved version of this agent.">
      {error && <Notice tone="error">{error}</Notice>}
      {schedules === null && !error && <p className="text-sm text-slate-600">Loading schedules...</p>}
      {schedules?.length === 0 && <p className="text-sm text-slate-600">Not scheduled.</p>}
      {!!schedules?.length && (
        <List>
          {schedules.map(s => (
            <Row
              key={s.id}
              title={<span className="font-normal break-words">{s.data.input}</span>}
              badges={<Badge><code>{s.cron_expression ?? 'once'}</code></Badge>}
              meta={[s.enabled ? `next ${formatRelativeTime(s.next_scheduled_time)}` : s.cron_expression ? 'paused' : 'done']}
              actions={<>
                {(s.enabled || s.cron_expression) && (
                  <Button size="sm" onClick={() => act(() => saveSchedule({ definitionId, enabled: !s.enabled, taskId: s.id }))}>
                    {s.enabled ? 'Pause' : 'Resume'}
                  </Button>
                )}
                <ConfirmButton confirm="Delete this schedule and its history?" onConfirm={() => act(() => deleteSchedule(s.id))} />
              </>}
            />
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

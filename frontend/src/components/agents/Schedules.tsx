import { useCallback, useEffect, useState } from 'react'
import { useAgents, type AgentSchedule } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { Badge, Button, ConfirmButton, EmptyState, Field, FieldRow, FormActions, Input, List, LoadingState, Notice, Row, Section, Textarea, useActionNotice, useSubmit } from '@/components/shared/ui'

// Only the shape is checked here; the server parses the fields themselves.
const isCron = (cron: string) => cron.trim().split(/\s+/).length === 5

export function Schedules({ definitionId }: { definitionId: number }) {
  const { listSchedules, saveSchedule, deleteSchedule } = useAgents()
  const [schedules, setSchedules] = useState<AgentSchedule[] | null>(null)
  const [cron, setCron] = useState('0 9 * * *')
  const [input, setInput] = useState('')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const { run, notice } = useActionNotice()
  const adding = useSubmit()

  const reload = useCallback(() => {
    setLoadError(null)
    return listSchedules(definitionId).then(setSchedules).catch(e => setLoadError(e.message))
  }, [listSchedules, definitionId])
  useEffect(() => { reload() }, [reload])

  const act = async (id: string, action: () => Promise<unknown>) => {
    setBusyId(id)
    await run(async () => { await action(); await reload() })
    setBusyId(null)
  }

  const add = () => adding.submit(async () => {
    await saveSchedule({ definitionId, input, cron })
    setInput('')
    await reload()
  })

  const cronError = cron.trim() && !isCron(cron) ? 'Use five fields: minute hour day-of-month month day-of-week' : null

  return (
    <Section title="Schedules" description="Each run uses the saved version of this agent.">
      {loadError && <Notice tone="error" onRetry={reload}>{loadError}</Notice>}
      {notice}
      {schedules === null && !loadError && <LoadingState message="Loading schedules..." />}
      {schedules?.length === 0 && <EmptyState message="No schedules yet." />}
      {!!schedules?.length && (
        <List>
          {schedules.map(s => (
            <Row
              key={s.id}
              title={<span className="font-normal break-words">{s.data.input}</span>}
              badges={<>
                <Badge><code>{s.cron_expression ?? 'once'}</code></Badge>
                {!s.enabled && <Badge>{s.cron_expression ? 'Paused' : 'Done'}</Badge>}
              </>}
              meta={[s.enabled && `next ${formatRelativeTime(s.next_scheduled_time)}`]}
              actions={<>
                {(s.enabled || s.cron_expression) && (
                  <Button size="sm" disabled={busyId !== null}
                    onClick={() => act(s.id, () => saveSchedule({ definitionId, enabled: !s.enabled, taskId: s.id }))}>
                    {busyId === s.id ? 'Saving...' : s.enabled ? 'Pause' : 'Resume'}
                  </Button>
                )}
                <ConfirmButton confirm="Delete this schedule and its history?" onConfirm={() => act(s.id, () => deleteSchedule(s.id))} />
              </>}
            />
          ))}
        </List>
      )}
      <FieldRow>
        <Field label="Cron (UTC)" hint="For example, 0 9 * * * runs every day at 09:00 UTC." error={cronError}>
          <Input className="font-mono" value={cron} onChange={e => setCron(e.target.value)} />
        </Field>
        <Field label="Run input">
          <Textarea className="h-16" placeholder="What should each run do?" value={input} onChange={e => setInput(e.target.value)} />
        </Field>
      </FieldRow>
      <FormActions>
        {adding.error && <Notice tone="error" className="basis-full">{adding.error}</Notice>}
        <Button variant="primary" disabled={adding.busy || !input.trim() || !isCron(cron)} onClick={add}>
          {adding.busy ? 'Saving...' : 'Schedule'}
        </Button>
      </FormActions>
    </Section>
  )
}

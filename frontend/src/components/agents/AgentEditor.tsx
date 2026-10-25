import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { defaultSpec, useAgents, type AgentRun, type AgentSpec, type Catalog } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { SpecEditor } from './SpecEditor'
import { StatusBadge } from './StatusBadge'
import { Schedules } from './Schedules'
import { Triggers } from './Triggers'
import { Button, ConfirmButton, Field, FieldRow, FormActions, Input, List, Notice, Page, Row, Section, Textarea, useActionNotice, useSubmit, useUnsavedGuard } from '@/components/shared/ui'
import { isForbiddenOrMissing } from '@/hooks/useMCP'
import { formatCost } from '@/utils/format'

// agent_fetch reports a missing agent with no status code; access denials and HTTP 403/404 arrive typed.
const UNKNOWN_AGENT = /unknown agent definition/i

const snapshot = (name: string, description: string, spec: AgentSpec | null) => JSON.stringify({ name, description, spec })

export default function AgentEditor() {
  const { id } = useParams()
  const isNew = id === 'new'
  const definitionId = isNew ? undefined : Number(id)
  const validId = isNew || Number.isInteger(definitionId)
  const navigate = useNavigate()
  const { catalog: loadCatalog, getDefinition, saveDefinition, deleteDefinition, startRun, listRuns } = useAgents()
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [spec, setSpec] = useState<AgentSpec | null>(null)
  const [runs, setRuns] = useState<AgentRun[]>([])
  const [input, setInput] = useState('')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [missing, setMissing] = useState(false)
  const [runsError, setRunsError] = useState<string | null>(null)
  const [valid, setValid] = useState(true)
  const [saved, setSaved] = useState<string | null>(null)
  const [changedTools, setChangedTools] = useState<string[]>([])
  const saving = useSubmit()
  const running = useSubmit()
  const { run: act, notice } = useActionNotice()
  const current = useMemo(() => snapshot(name, description, spec), [name, description, spec])
  const dirty = saved !== null && current !== saved
  const { dialog: leaveDialog, bypass } = useUnsavedGuard(dirty, `Discard unsaved changes to ${name || 'this agent'}?`)

  const load = useCallback(() => {
    if (!validId) return
    setLoadError(null)
    loadCatalog()
      .then(c => {
        setCatalog(c)
        if (!isNew) return
        setSpec(defaultSpec(c.default_model))
        setSaved(snapshot('', '', defaultSpec(c.default_model)))
      })
      .catch(e => setLoadError(e.message))
    if (definitionId === undefined) return
    getDefinition(definitionId)
      .then(d => {
        setName(d.name)
        setDescription(d.description)
        setSpec(d.spec)
        setChangedTools(d.changed_tools ?? [])
        setSaved(snapshot(d.name, d.description, d.spec))
      })
      .catch(e => isForbiddenOrMissing(e) || UNKNOWN_AGENT.test(e.message) ? setMissing(true) : setLoadError(e.message))
  }, [loadCatalog, getDefinition, isNew, definitionId, validId])
  useEffect(load, [load])

  const loadRuns = useCallback(() => {
    if (definitionId === undefined || !validId) return
    setRunsError(null)
    listRuns({ definitionId, limit: 20 }).then(setRuns).catch(e => setRunsError(e.message))
  }, [listRuns, definitionId, validId])
  useEffect(loadRuns, [loadRuns])

  const save = () => saving.submit(async () => {
    if (!spec) return
    const stored = await saveDefinition({ name: name.trim(), description, spec, id: definitionId })
    setName(stored.name)
    setSpec(stored.spec)
    setChangedTools([])
    setSaved(snapshot(stored.name, stored.description, stored.spec))
    if (isNew) bypass(() => navigate(`/ui/agents/${stored.id}`))
  })

  const run = () => running.submit(async () => {
    const { run_id } = await startRun({ input, definitionId })
    navigate(`/ui/agents/runs/${run_id}?from=agent`)
  })

  const remove = () => act(async () => {
    if (definitionId === undefined) return
    await deleteDefinition(definitionId)
    bypass(() => navigate('/ui/agents'))
  })

  const back = { to: '/ui/agents', label: 'Agents' }
  if (!validId || missing) return <Page back={back} title="Agent" error="This agent doesn't exist or isn't yours" />
  if (!catalog || !spec) return <Page back={back} title={isNew ? 'New agent' : 'Agent'} loading="Loading agent..." error={loadError} onRetry={load} />

  return (
    <Page back={back} title={name || (isNew ? 'New agent' : 'Untitled agent')}
      actions={!isNew && <ConfirmButton size="md" confirm={`Delete agent "${name}"? Its runs are kept.`} onConfirm={remove}>Delete agent</ConfirmButton>}>
      {notice}
      {leaveDialog}
      <FieldRow>
        <Field label="Name"><Input required maxLength={100} value={name} onChange={e => setName(e.target.value)} /></Field>
        <Field label="Description"><Input value={description} onChange={e => setDescription(e.target.value)} /></Field>
      </FieldRow>
      {changedTools.length > 0 && (
        <Notice tone="warning">
          These external tools changed since this agent was saved: {changedTools.join(', ')}. Check them on their
          server's page, then save the agent again to accept the change.
        </Notice>
      )}
      <SpecEditor value={spec} catalog={catalog} onChange={setSpec} onValidityChange={setValid} />
      <FormActions className="items-center pb-6 border-b border-slate-200">
        {saving.error && <Notice tone="error" className="basis-full">{saving.error}</Notice>}
        {!isNew && dirty && <span className="text-xs text-slate-600">Unsaved changes</span>}
        <Button variant="primary" disabled={saving.busy || !valid || !name.trim() || (!dirty && changedTools.length === 0)} onClick={save}>
          {saving.busy ? 'Saving...' : isNew ? 'Create agent' : 'Save'}
        </Button>
      </FormActions>
      {!isNew && (
        <Section title="Run">
          <Field label="Run input">
            <Textarea className="h-24" placeholder="What should the agent do?" value={input} onChange={e => setInput(e.target.value)} />
          </Field>
          {running.error && <Notice tone="error">{running.error}</Notice>}
          <div className="flex items-center gap-2">
            <Button variant="primary" disabled={running.busy || !input.trim() || dirty} onClick={run}>
              {running.busy ? 'Starting...' : 'Run saved version'}
            </Button>
            {dirty && <span className="text-xs text-slate-600">Save your changes to run them.</span>}
          </div>
          {runsError && <Notice tone="error" onRetry={loadRuns}>{runsError}</Notice>}
          {!!runs.length && (
            <List>
              {runs.map(r => (
                <Row key={r.id} to={`/ui/agents/runs/${r.id}?from=agent`} title={r.input} badges={<StatusBadge status={r.status} />}
                  meta={[formatRelativeTime(r.created_at), formatCost(r.cost_usd)]} />
              ))}
            </List>
          )}
        </Section>
      )}
      {definitionId !== undefined && <Schedules definitionId={definitionId} />}
      {definitionId !== undefined && <Triggers definitionId={definitionId} sourceTypes={catalog.trigger_source_types} />}
    </Page>
  )
}

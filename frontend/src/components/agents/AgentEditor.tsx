import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { defaultSpec, useAgents, type AgentRun, type AgentSpec, type Catalog } from '@/hooks/useAgents'
import { formatRelativeTime } from '@/components/sources/shared'
import { SpecEditor } from './SpecEditor'
import { StatusBadge } from './StatusBadge'
import { Schedules } from './Schedules'
import { Triggers } from './Triggers'
import { Button, Field, FieldRow, Input, List, ListItem, Notice, Page, Section, Textarea } from '@/components/shared/ui'

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
  const [error, setError] = useState<string | null>(null)
  const [jsonValid, setJsonValid] = useState(true)
  const [saved, setSaved] = useState<string | null>(null)
  const [changedTools, setChangedTools] = useState<string[]>([])
  const current = useMemo(() => JSON.stringify({ name, description, spec }), [name, description, spec])
  const dirty = current !== saved

  useEffect(() => {
    loadCatalog()
      .then(c => {
        setCatalog(c)
        if (isNew) setSpec(defaultSpec(c.default_model))
      })
      .catch(e => setError(e.message))
  }, [loadCatalog, isNew])

  useEffect(() => {
    if (definitionId === undefined || !validId) return
    getDefinition(definitionId)
      .then(d => {
        setName(d.name)
        setDescription(d.description)
        setSpec(d.spec)
        setChangedTools(d.changed_tools ?? [])
        setSaved(JSON.stringify({ name: d.name, description: d.description, spec: d.spec }))
      })
      .catch(e => setError(e.message))
    listRuns({ definitionId, limit: 20 }).then(setRuns).catch(e => setError(e.message))
  }, [definitionId, validId, getDefinition, listRuns])

  const save = async () => {
    if (!spec) return
    setError(null)
    try {
      const stored = await saveDefinition({ name, description, spec, id: definitionId })
      setName(stored.name)
      setSpec(stored.spec)
      setChangedTools([])
      setSaved(JSON.stringify({ name: stored.name, description: stored.description, spec: stored.spec }))
      if (isNew) navigate(`/ui/agents/${stored.id}`)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const run = async () => {
    setError(null)
    try {
      const { run_id } = await startRun({ input, definitionId })
      navigate(`/ui/agents/runs/${run_id}`)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const remove = async () => {
    if (definitionId === undefined || !window.confirm(`Delete agent "${name}"? Its runs are kept.`)) return
    try {
      await deleteDefinition(definitionId)
      navigate('/ui/agents')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const back = { to: '/ui/agents', label: 'Agents' }
  if (!validId) return <Page back={back} title="Unknown agent" />
  if (!catalog || !spec) return <Page back={back} title={isNew ? 'New agent' : 'Agent'} loading="Loading agent..." error={error} />

  return (
    <Page back={back} title={name || (isNew ? 'New agent' : 'Untitled agent')}>
      {error && <Notice tone="error">{error}</Notice>}
      <FieldRow>
        <Field label="Name"><Input maxLength={100} value={name} onChange={e => setName(e.target.value)} /></Field>
        <Field label="Description"><Input value={description} onChange={e => setDescription(e.target.value)} /></Field>
      </FieldRow>
      {changedTools.length > 0 && (
        <Notice tone="warning">
          These external tools changed since this agent was saved: {changedTools.join(', ')}. Check them on their
          server's page, then save the agent again to accept the change.
        </Notice>
      )}
      <SpecEditor value={spec} catalog={catalog} onChange={setSpec} onValidityChange={setJsonValid} />
      <div className="flex flex-wrap items-center gap-2 pb-6 border-b border-slate-200">
        <Button variant="primary" disabled={!jsonValid || (!dirty && changedTools.length === 0)} onClick={save}>
          {isNew ? 'Create agent' : 'Save'}
        </Button>
        {!isNew && dirty && <span className="text-xs text-slate-600">Unsaved changes</span>}
        {!isNew && <Button variant="danger" className="ml-auto" onClick={remove}>Delete agent</Button>}
      </div>
      {!isNew && (
        <Section title="Run">
          <Textarea aria-label="Run input" className="h-24" placeholder="What should the agent do?"
            value={input} onChange={e => setInput(e.target.value)} />
          <div className="flex items-center gap-2">
            <Button variant="primary" disabled={!input.trim() || dirty} onClick={run}>Run saved version</Button>
            {dirty && <span className="text-xs text-slate-600">Save your changes to run them.</span>}
          </div>
          {!!runs.length && (
            <List>
              {runs.map(r => (
                <ListItem key={r.id}>
                  <Link to={`/ui/agents/runs/${r.id}`} className="text-primary truncate min-w-0 flex-1">{r.input}</Link>
                  <span className="flex items-center gap-2 shrink-0 text-xs text-slate-600">
                    <StatusBadge status={r.status} />
                    {formatRelativeTime(r.created_at)} · ${r.cost_usd}
                  </span>
                </ListItem>
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
